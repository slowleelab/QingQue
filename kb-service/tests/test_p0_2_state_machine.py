"""P0-2.2 takedown/rollback 走状态机验证 (10 用例)

覆盖:
- T1 takedown DRAFT 文档 → 422 (状态机拒绝, 旧代码允许)
- T2 takedown PUBLISHED 文档 → 200, approval_status=ARCHIVED, KbDocumentApproval 写库
- T3 takedown ARCHIVED 文档 → 422 (终态, 状态机拒绝)
- T4 takedown 必填 comment (ARCHIVE 在状态机里强制)
- T5 takedown reason 必须是 regulatory/security/quality/other
- T6 rollback target 非 PUBLISHED → 422 (状态机拒绝)
- T7 rollback admin 操作 → 200, KbDocumentApproval.action=SUPERSEDE
- T8 rollback 只写 1 条 KbDocumentApproval (target doc 的 SUPERSEDE)
- T9 rollback is_current_version 在两 doc 间正确切换 (PG 行为不变)
- T10 takedown + rollback 的 KbDocumentApproval.tenant_id 取自 doc.tenant_id

注意: 全部用 monkeypatch.fixtures 而非 class-level monkey-patch, 避免泄漏
到其他测试 (test_p0_tenant_isolation.py 等).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kb.api.documents import TakedownRequest, RollbackRequest
from kb.orm.kb import KbApprovalAction, KbApprovalStatus


# ── helper: 构造 fake KbDocument + db ──


def _make_doc(
    *,
    doc_id: str | None = None,
    tenant_id: str = "default",
    approval_status_value: str = "PUBLISHED",
    is_current: bool = True,
    doc_group: str = "g1",
    version: str = "1.0",
    created_by: str = "alice",
) -> MagicMock:
    if doc_id is None:
        doc_id = str(uuid.uuid4())  # 真 UUID, 满足 uuid_utils.UUID 解析
    doc = MagicMock()
    doc.id = doc_id
    doc.tenant_id = tenant_id
    doc.approval_status = MagicMock()
    doc.approval_status.value = approval_status_value
    doc.is_current_version = is_current
    doc.doc_group = doc_group
    doc.version = version
    doc.created_by = created_by
    doc.is_deleted = False
    doc.created_at = dt.datetime.now(dt.UTC)
    doc.updated_at = doc.created_at
    doc.chunk_count = 0
    return doc


def _make_db(docs: dict[str, MagicMock]) -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()

    async def fake_get(_model, pk):
        return docs.get(str(pk))

    db.get = fake_get

    # P0-3.B: get_last_actor 调 db.execute(select(KbDocumentApproval.actor_id))
    # 默认返回 None → last_actor 走 doc.created_by 兜底
    async def fake_execute(stmt):
        return MagicMock(scalar_one_or_none=MagicMock(return_value=None))

    db.execute = fake_execute

    # Capture KbDocumentApproval added
    db.approvals_added: list[Any] = []

    def capture_add(obj):
        cls_name = obj.__class__.__name__
        if cls_name == "KbDocumentApproval":
            db.approvals_added.append(obj)
        else:
            pass  # default

    db.add.side_effect = capture_add
    return db


def _admin_principal() -> MagicMock:
    p = MagicMock()
    p.actor_id = "admin_user"
    p.actor_role = "admin"
    p.roles = ["admin"]
    p.tenant_id = "default"
    return p


def _make_request() -> MagicMock:
    """P0-3.A: fake Request for IP/UA/request_id 提取"""
    req = MagicMock()
    req.client = MagicMock()
    req.client.host = "10.0.0.1"
    req.headers = {"user-agent": "test-agent/1.0", "x-request-id": "test-rid-1"}
    req.state = MagicMock()
    req.state.request_id = None
    return req


# ═══════════════════════════════════════════════════════════════════════
# T1 takedown DRAFT 文档 → 422
# ═══════════════════════════════════════════════════════════════════════


class TestTakedownStateMachine:
    @pytest.mark.asyncio
    async def test_takedown_draft_rejected(self):
        """旧代码允许 DRAFT 直接 takedown, C2 走状态机后 DRAFT 必须先 publish 再 takedown"""
        from fastapi import HTTPException
        from kb.api.documents import emergency_takedown

        doc = _make_doc(approval_status_value="DRAFT")
        db = _make_db({doc.id: doc})
        principal = _admin_principal()
        payload = TakedownRequest(comment="合规问题紧急下架", reason="regulatory")

        with pytest.raises(HTTPException) as exc:
            await emergency_takedown(doc.id, payload, _make_request(), db, principal)
        assert exc.value.status_code == 422
        # 状态机报错: 状态 DRAFT 不能执行 ARCHIVE
        assert "DRAFT" in exc.value.detail
        assert "ARCHIVE" in exc.value.detail

    @pytest.mark.asyncio
    async def test_takedown_published_succeeds(self):
        """T2: takedown PUBLISHED → ARCHIVED, 写 KbDocumentApproval"""
        from kb.api.documents import emergency_takedown
        from kb.security.audit_service import AuditService

        doc = _make_doc(approval_status_value="PUBLISHED")
        db = _make_db({doc.id: doc})

        # mock AuditService (function-scoped, 不会泄漏)
        monkey = pytest.MonkeyPatch()
        monkey.setattr(AuditService, "__init__", MagicMock(return_value=None))
        monkey.setattr(AuditService, "log", AsyncMock())

        try:
            principal = _admin_principal()
            payload = TakedownRequest(comment="合规问题紧急下架", reason="regulatory")

            response = await emergency_takedown(doc.id, payload, _make_request(), db, principal)

            assert response.actor_id == "admin_user"
            assert response.reason == "regulatory"
            # doc 状态变更
            assert doc.approval_status.value == "ARCHIVED"
            assert doc.is_current_version is False
            # 1 条 KbDocumentApproval
            assert len(db.approvals_added) == 1
            record = db.approvals_added[0]
            assert record.action == KbApprovalAction.ARCHIVE
            assert record.from_status == "PUBLISHED"
            assert record.to_status == "ARCHIVED"
            assert record.risk_level == "high"
            assert "[TAKEDOWN/regulatory]" in record.comment
            assert record.tenant_id == "default"
        finally:
            monkey.undo()

    @pytest.mark.asyncio
    async def test_takedown_archived_rejected(self):
        """T3: 终态 ARCHIVED 由状态机拒绝 (而非旧代码 explicit check)"""
        from fastapi import HTTPException
        from kb.api.documents import emergency_takedown

        doc = _make_doc(approval_status_value="ARCHIVED")
        db = _make_db({doc.id: doc})
        principal = _admin_principal()
        payload = TakedownRequest(comment="合规问题紧急下架", reason="regulatory")

        with pytest.raises(HTTPException) as exc:
            await emergency_takedown(doc.id, payload, _make_request(), db, principal)
        assert exc.value.status_code == 422
        # 状态机报错
        assert "ARCHIVED" in exc.value.detail

    @pytest.mark.asyncio
    async def test_takedown_requires_comment(self):
        """T4: ARCHIVE 在状态机里强制 comment 非空 (Pydantic 422 也行, 状态机 422 也行)"""
        from fastapi import HTTPException
        from kb.api.documents import emergency_takedown
        from pydantic import ValidationError

        # Pydantic min_length=5 先拦住
        with pytest.raises(ValidationError):
            TakedownRequest(comment="", reason="other")

        # 即使绕开 Pydantic (comment="   " 全空白), 状态机也会拦
        doc = _make_doc(approval_status_value="PUBLISHED")
        db = _make_db({doc.id: doc})
        principal = _admin_principal()
        # 用 model_construct 绕开 Pydantic 校验
        payload = TakedownRequest.model_construct(comment="   ", reason="other")

        with pytest.raises(HTTPException) as exc:
            await emergency_takedown(doc.id, payload, _make_request(), db, principal)
        # 状态机 comment_required → 422
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_takedown_invalid_reason(self):
        """T5: reason 枚举校验 (在端点内运行时校验, 不是 Pydantic)"""
        from fastapi import HTTPException
        from kb.api.documents import emergency_takedown

        doc = _make_doc(approval_status_value="PUBLISHED")
        db = _make_db({doc.id: doc})
        principal = _admin_principal()
        # model_construct 绕开 default, 传无效 reason
        payload = TakedownRequest.model_construct(comment="合规问题紧急下架", reason="bogus")

        with pytest.raises(HTTPException) as exc:
            await emergency_takedown(doc.id, payload, _make_request(), db, principal)
        assert exc.value.status_code == 422
        assert "reason" in exc.value.detail.lower() or "regulatory" in exc.value.detail.lower()


# ═══════════════════════════════════════════════════════════════════════
# T6-T10 rollback 走状态机
# ═══════════════════════════════════════════════════════════════════════


class TestRollbackStateMachine:
    @pytest.mark.asyncio
    async def test_rollback_target_not_published_rejected(self):
        """T6: target 非 PUBLISHED → 422 (状态机拒绝)"""
        from fastapi import HTTPException
        from kb.api.documents import rollback_document

        current = _make_doc(is_current=True, approval_status_value="PUBLISHED")
        target = _make_doc(is_current=False, approval_status_value="DRAFT")
        db = _make_db({current.id: current, target.id: target})
        principal = _admin_principal()

        request = RollbackRequest(target_doc_id=target.id, comment="回滚测试")
        with pytest.raises(HTTPException) as exc:
            await rollback_document(current.id, request, _make_request(), db, principal)
        assert exc.value.status_code == 422
        # 状态机报错: DRAFT 不能执行 SUPERSEDE
        assert "DRAFT" in exc.value.detail or "已发布" in exc.value.detail

    @pytest.mark.asyncio
    async def test_rollback_admin_succeeds(self):
        """T7: admin 操作 rollback → 200, KbDocumentApproval.action=SUPERSEDE"""
        from kb.api.documents import rollback_document
        from kb.security.audit_service import AuditService

        current = _make_doc(is_current=True, approval_status_value="PUBLISHED")
        target = _make_doc(is_current=False, approval_status_value="PUBLISHED")
        db = _make_db({current.id: current, target.id: target})

        monkey = pytest.MonkeyPatch()
        monkey.setattr(AuditService, "__init__", MagicMock(return_value=None))
        monkey.setattr(AuditService, "log", AsyncMock())

        try:
            principal = _admin_principal()
            request = RollbackRequest(target_doc_id=target.id, comment="回滚到 v1")
            response = await rollback_document(current.id, request, _make_request(), db, principal)

            assert response.from_doc_id == current.id
            assert response.to_doc_id == target.id
        finally:
            monkey.undo()

    @pytest.mark.asyncio
    async def test_rollback_writes_one_approval(self):
        """T8: rollback 只写 1 条 KbDocumentApproval (target doc 的 SUPERSEDE)"""
        from kb.api.documents import rollback_document
        from kb.security.audit_service import AuditService

        current = _make_doc(is_current=True, approval_status_value="PUBLISHED")
        target = _make_doc(is_current=False, approval_status_value="PUBLISHED")
        db = _make_db({current.id: current, target.id: target})

        monkey = pytest.MonkeyPatch()
        monkey.setattr(AuditService, "__init__", MagicMock(return_value=None))
        monkey.setattr(AuditService, "log", AsyncMock())

        try:
            principal = _admin_principal()
            request = RollbackRequest(target_doc_id=target.id, comment="rollback test")
            await rollback_document(current.id, request, _make_request(), db, principal)

            assert len(db.approvals_added) == 1
            record = db.approvals_added[0]
            assert record.action == KbApprovalAction.SUPERSEDE
            # 记录挂在 target doc 上
            assert str(record.document_id) == target.id
            assert "ROLLBACK" in record.comment
            assert record.risk_level == "high"
        finally:
            monkey.undo()

    @pytest.mark.asyncio
    async def test_rollback_switches_current_version(self):
        """T9: rollback is_current_version 在两 doc 间正确切换 (PG 行为)"""
        from kb.api.documents import rollback_document
        from kb.security.audit_service import AuditService

        current = _make_doc(is_current=True, approval_status_value="PUBLISHED")
        target = _make_doc(is_current=False, approval_status_value="PUBLISHED")
        db = _make_db({current.id: current, target.id: target})

        monkey = pytest.MonkeyPatch()
        monkey.setattr(AuditService, "__init__", MagicMock(return_value=None))
        monkey.setattr(AuditService, "log", AsyncMock())

        try:
            principal = _admin_principal()
            request = RollbackRequest(target_doc_id=target.id, comment="rollback")
            await rollback_document(current.id, request, _make_request(), db, principal)

            assert current.is_current_version is False
            assert target.is_current_version is True
        finally:
            monkey.undo()

    @pytest.mark.asyncio
    async def test_rollback_tenant_id_from_doc(self):
        """T10: KbDocumentApproval.tenant_id 取自 target doc.tenant_id"""
        from kb.api.documents import rollback_document
        from kb.security.audit_service import AuditService

        current = _make_doc(
            is_current=True, approval_status_value="PUBLISHED", tenant_id="default",
        )
        target = _make_doc(
            is_current=False, approval_status_value="PUBLISHED", tenant_id="bank-prod",
        )
        db = _make_db({current.id: current, target.id: target})

        monkey = pytest.MonkeyPatch()
        monkey.setattr(AuditService, "__init__", MagicMock(return_value=None))
        monkey.setattr(AuditService, "log", AsyncMock())

        try:
            principal = _admin_principal()
            request = RollbackRequest(target_doc_id=target.id, comment="rollback")
            await rollback_document(current.id, request, _make_request(), db, principal)

            assert len(db.approvals_added) == 1
            record = db.approvals_added[0]
            # 即使 principal 是 default, KbDocumentApproval 仍记录 doc 自己的 tenant (合规留痕)
            assert record.tenant_id == "bank-prod"
        finally:
            monkey.undo()
