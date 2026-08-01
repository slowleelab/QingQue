"""I2-C3 版本回滚 / 紧急下架 / diff E2E 测试

覆盖:
- GET /documents/{id}/versions 列出 doc_group 下所有版本
- POST /documents/{id}/rollback 原子切换 is_current_version
- GET /documents/{id}/diff 字段级 diff
- POST /documents/{id}/takedown 紧急下架, admin only, 强留痕
- 权限: rollback/takedown 仅 admin/service
- 跨 doc_group 拒绝 rollback/diff
- takedown 重复操作拒绝
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ── helper: 真实 UUID 风格 ID 池 ──


def _uuid_str(seed: str) -> str:
    """基于 seed 生成稳定 UUID (v5 风格)"""
    import uuid as _uuid

    return str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"kb-test:{seed}"))


# 预定义 doc ID
DOC_V1 = _uuid_str("doc-v1")
DOC_V2 = _uuid_str("doc-v2")
DOC_X = _uuid_str("doc-x")


def _make_db_with_docs(docs: dict[str, MagicMock]) -> MagicMock:
    """构造带 doc 字典的 mock db"""
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()

    async def fake_get(model, pk):
        return docs.get(str(pk))

    db.get = fake_get
    return db


def _make_version_doc(
    doc_id: str,
    *,
    doc_group: str = "g1",
    version: str = "1.0",
    is_current: bool = True,
    approval_status: str = "PUBLISHED",
    content_hash: str = "hash_abc",
    tenant_id: str = "default",
    created_by: str = "alice",
) -> MagicMock:
    """构造一个 mock KbDocument, 含 rollback/diff 需要的字段"""
    import datetime as _dt

    doc = MagicMock()
    doc.id = doc_id
    doc.doc_group = doc_group
    doc.version = version
    doc.is_current_version = is_current
    doc.approval_status = MagicMock()
    doc.approval_status.value = approval_status
    doc.content_hash = content_hash
    doc.tenant_id = tenant_id
    doc.created_by = created_by
    doc.is_deleted = False
    doc.created_at = _dt.datetime.now(_dt.timezone.utc)
    doc.updated_at = doc.created_at
    doc.llm_summary = "test summary"
    doc.llm_keywords = ["k1", "k2"]
    doc.llm_entities = ["e1"]
    doc.effective_date = None
    doc.expiry_date = None
    doc.chunk_count = 5
    return doc


# ── versions 列表 ──


class TestListVersions:
    @pytest.mark.asyncio
    async def test_list_versions_returns_all_in_group(self):
        from kb.api.documents import list_versions

        current = _make_version_doc(DOC_V2, doc_group="g1", version="2.0", is_current=True)
        v1 = _make_version_doc(DOC_V1, doc_group="g1", version="1.0", is_current=False)

        db = MagicMock()

        async def fake_get(model, pk):
            return current

        result = MagicMock()
        result.scalars.return_value.all = MagicMock(return_value=[current, v1])
        db.get = fake_get
        db.execute = AsyncMock(return_value=result)

        principal = MagicMock()
        principal.tenant_id = "default"

        response = await list_versions(DOC_V2, db, principal)
        assert response.doc_group == "g1"
        assert response.current_doc_id == DOC_V2
        assert len(response.versions) == 2
        assert response.versions[0].is_current is True
        assert response.versions[0].version == "2.0"

    @pytest.mark.asyncio
    async def test_list_versions_invalid_doc_id(self):
        from kb.api.documents import list_versions
        from fastapi import HTTPException

        db = MagicMock()
        principal = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await list_versions("not-a-uuid", db, principal)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_list_versions_404_when_doc_missing(self):
        from kb.api.documents import list_versions
        from fastapi import HTTPException

        db = MagicMock()
        db.get = AsyncMock(return_value=None)
        principal = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await list_versions(DOC_V1, db, principal)
        assert exc.value.status_code == 404


# ── rollback ──


class TestRollbackDocument:
    @pytest.mark.asyncio
    async def test_rollback_atomic_switch(self):
        from kb.api.documents import rollback_document, RollbackRequest

        current = _make_version_doc(DOC_V2, doc_group="g1", version="2.0", is_current=True)
        target = _make_version_doc(DOC_V1, doc_group="g1", version="1.0", is_current=False)

        db = _make_db_with_docs({DOC_V2: current, DOC_V1: target})

        from kb.security.audit_service import AuditService

        AuditService.__init__ = MagicMock(return_value=None)
        AuditService.log = AsyncMock()

        principal = MagicMock()
        principal.actor_id = "admin_user"
        principal.actor_role = "admin"
        principal.roles = ["admin"]
        principal.tenant_id = "default"

        request = RollbackRequest(target_doc_id=DOC_V1, comment="回滚到 v1")
        response = await rollback_document(DOC_V2, request, db, principal)

        assert current.is_current_version is False
        assert target.is_current_version is True
        assert response.from_version == "2.0"
        assert response.to_version == "1.0"
        assert db.add.called

    @pytest.mark.asyncio
    async def test_rollback_cross_group_rejected(self):
        from kb.api.documents import rollback_document, RollbackRequest
        from fastapi import HTTPException

        current = _make_version_doc(DOC_V2, doc_group="g1", version="2.0")
        target = _make_version_doc(DOC_X, doc_group="g-other", version="1.0")

        db = _make_db_with_docs({DOC_V2: current, DOC_X: target})

        principal = MagicMock()
        principal.actor_id = "admin_user"
        principal.actor_role = "admin"
        principal.roles = ["admin"]

        request = RollbackRequest(target_doc_id=DOC_X, comment="test")
        with pytest.raises(HTTPException) as exc:
            await rollback_document(DOC_V2, request, db, principal)
        assert exc.value.status_code == 422
        assert "文档组" in exc.value.detail or "doc_group" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_rollback_to_unpublished_rejected(self):
        from kb.api.documents import rollback_document, RollbackRequest
        from fastapi import HTTPException

        current = _make_version_doc(DOC_V2, doc_group="g1", version="2.0", is_current=True)
        target = _make_version_doc(DOC_V1, doc_group="g1", version="1.0", is_current=False, approval_status="DRAFT")

        db = _make_db_with_docs({DOC_V2: current, DOC_V1: target})
        principal = MagicMock()
        principal.roles = ["admin"]
        principal.actor_role = "admin"

        request = RollbackRequest(target_doc_id=DOC_V1, comment="x")
        with pytest.raises(HTTPException) as exc:
            await rollback_document(DOC_V2, request, db, principal)
        assert exc.value.status_code == 422
        assert "已发布" in exc.value.detail or "PUBLISHED" in exc.value.detail

    @pytest.mark.asyncio
    async def test_rollback_requires_admin(self):
        from kb.api.documents import rollback_document, RollbackRequest
        from fastapi import HTTPException

        current = _make_version_doc(DOC_V2, doc_group="g1", version="2.0", is_current=True)
        target = _make_version_doc(DOC_V1, doc_group="g1", version="1.0", is_current=False, approval_status="PUBLISHED")

        db = _make_db_with_docs({DOC_V2: current, DOC_V1: target})
        principal = MagicMock()
        principal.roles = ["editor"]
        principal.actor_role = "editor"
        principal.actor_id = "alice"

        request = RollbackRequest(target_doc_id=DOC_V1, comment="x")
        with pytest.raises(HTTPException) as exc:
            await rollback_document(DOC_V2, request, db, principal)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_rollback_comment_required(self):
        from kb.api.documents import RollbackRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RollbackRequest(target_doc_id=DOC_V1, comment="")


# ── diff ──


class TestDiffVersions:
    @pytest.mark.asyncio
    async def test_diff_returns_field_changes(self):
        from kb.api.documents import diff_versions

        from_doc = _make_version_doc(DOC_V1, doc_group="g1", version="1.0", content_hash="hash_old")
        to_doc = _make_version_doc(DOC_V2, doc_group="g1", version="2.0", content_hash="hash_new", is_current=True)

        db = _make_db_with_docs({DOC_V1: from_doc, DOC_V2: to_doc})

        principal = MagicMock()
        response = await diff_versions(DOC_V1, db, principal, from_doc_id=DOC_V1, to_doc_id=DOC_V2)

        assert response.from_doc_id == DOC_V1
        assert response.to_doc_id == DOC_V2
        version_field = next(f for f in response.fields if f.field == "version")
        assert version_field.changed is True
        assert version_field.from_value == "1.0"
        assert version_field.to_value == "2.0"
        assert response.content_unified_diff is not None

    @pytest.mark.asyncio
    async def test_diff_cross_group_rejected(self):
        from kb.api.documents import diff_versions
        from fastapi import HTTPException

        from_doc = _make_version_doc(DOC_V1, doc_group="g1", version="1.0")
        to_doc = _make_version_doc(DOC_X, doc_group="g-other", version="2.0")

        db = _make_db_with_docs({DOC_V1: from_doc, DOC_X: to_doc})
        principal = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await diff_versions(DOC_V1, db, principal, from_doc_id=DOC_V1, to_doc_id=DOC_X)
        assert exc.value.status_code == 422


# ── takedown ──


class TestEmergencyTakedown:
    @pytest.mark.asyncio
    async def test_takedown_archives_and_records(self):
        from kb.api.documents import emergency_takedown, TakedownRequest
        from kb.security.audit_service import AuditService

        doc = _make_version_doc(DOC_V1, doc_group="g1", version="1.0", is_current=True, approval_status="PUBLISHED")
        db = _make_db_with_docs({DOC_V1: doc})

        AuditService.__init__ = MagicMock(return_value=None)
        AuditService.log = AsyncMock()

        principal = MagicMock()
        principal.actor_id = "admin_user"
        principal.actor_role = "admin"
        principal.roles = ["admin"]

        payload = TakedownRequest(comment="合规问题紧急下架", reason="regulatory")
        response = await emergency_takedown(DOC_V1, payload, db, principal)

        assert response.actor_id == "admin_user"
        assert response.reason == "regulatory"
        assert doc.approval_status.value == "ARCHIVED"
        assert doc.is_current_version is False
        assert db.add.called

    @pytest.mark.asyncio
    async def test_takedown_requires_admin(self):
        from kb.api.documents import emergency_takedown, TakedownRequest
        from fastapi import HTTPException

        doc = _make_version_doc(DOC_V1, approval_status="PUBLISHED")
        db = _make_db_with_docs({DOC_V1: doc})

        principal = MagicMock()
        principal.roles = ["editor"]
        principal.actor_role = "editor"

        payload = TakedownRequest(comment="test reason long enough", reason="other")
        with pytest.raises(HTTPException) as exc:
            await emergency_takedown(DOC_V1, payload, db, principal)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_takedown_duplicate_rejected(self):
        from kb.api.documents import emergency_takedown, TakedownRequest
        from fastapi import HTTPException

        doc = _make_version_doc(DOC_V1, approval_status="ARCHIVED")
        db = _make_db_with_docs({DOC_V1: doc})

        principal = MagicMock()
        principal.roles = ["admin"]
        principal.actor_role = "admin"

        payload = TakedownRequest(comment="test reason long enough", reason="other")
        with pytest.raises(HTTPException) as exc:
            await emergency_takedown(DOC_V1, payload, db, principal)
        assert exc.value.status_code == 422
        assert "下架" in exc.value.detail

    @pytest.mark.asyncio
    async def test_takedown_comment_min_length(self):
        from kb.api.documents import TakedownRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TakedownRequest(comment="abc", reason="other")
