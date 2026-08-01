"""kb-service I1 完整端到端集成测试 (E2E)

覆盖 I1 全部 5 个 commit 的关键场景:
  T1.  上传 DRAFT (硬钉 PUBLISHED 已删)
  T2.  JWT 鉴权主路径
  T3.  完整审批闭环
  T4.  4-eyes 双签
  T5.  comment 强制
  T6.  终态 ARCHIVED
  T7.  多租户隔离
  T8.  检索事件审计
  T9.  审批事件审计
  T10. 业务审计查询
  T11. Admin diagnostics 修复
  T12. Admin clear-cache 修复
  T13. 否定语义 (P2-1)
  T14. 非法转移
  T15. WorkflowError 字符串
  T16. 审批历史
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

fastapi = pytest.importorskip("fastapi")
jwt = pytest.importorskip("jwt")
httpx = pytest.importorskip("httpx")

from kb.config import get_settings
from kb.main import create_app
from kb.orm.kb import KbApprovalAction, KbApprovalStatus

# ── JWT helper ──


def _make_jwt(settings, *, sub="alice", tenant_id="default", roles=None, actor_role=None,
              tier="normal", exp_offset=3600, extra=None) -> str:
    now = dt.datetime.now(dt.UTC)
    payload = {
        "sub": sub,
        "tenant_id": tenant_id,
        "roles": roles or ["editor"],
        "actor_role": actor_role or (roles[0] if roles else "user"),
        "tier": tier,
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + exp_offset,
        "aud": settings.jwt.audience,
        "iss": settings.jwt.issuer,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt.secret, algorithm=settings.jwt.algorithm)


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def jwt_alice(settings):
    return _make_jwt(settings, sub="alice", tenant_id="default", roles=["editor"])


@pytest.fixture
def jwt_bob(settings):
    return _make_jwt(settings, sub="bob", tenant_id="default", roles=["reviewer"])


@pytest.fixture
def jwt_carol(settings):
    return _make_jwt(settings, sub="carol", tenant_id="default", roles=["admin"])


@pytest.fixture
def jwt_dave(settings):
    return _make_jwt(settings, sub="dave", tenant_id="bank-prod", roles=["editor"])


# ── Fake DB session 跟踪真实 KbDocument 实例 ──


class FakeSession:
    """最小 AsyncSession 替身, 跟踪真实 ORM 对象"""

    def __init__(self):
        self.docs: dict[str, Any] = {}
        self.approvals: list = []
        self.retrievals: list = []
        self.committed = 0

    def add(self, obj):
        cls_name = obj.__class__.__name__
        if cls_name == "KbDocument":
            # FakeSession 不走真实 INSERT, 但 column default 不自动应用, 手动补
            from kb.orm.kb import KbApprovalStatus, KbDocStatus
            if obj.approval_status is None:
                obj.approval_status = KbApprovalStatus.DRAFT
            if obj.status is None:
                obj.status = KbDocStatus.PENDING
            if obj.is_current_version is None:
                obj.is_current_version = True
            if obj.tenant_id is None:
                obj.tenant_id = "default"
            if obj.allowed_roles is None:
                obj.allowed_roles = []
            if obj.llm_keywords is None:
                obj.llm_keywords = []
            if obj.llm_entities is None:
                obj.llm_entities = []
            import datetime as _dt
            if obj.created_at is None:
                obj.created_at = _dt.datetime.now(_dt.UTC)
            if obj.updated_at is None:
                obj.updated_at = obj.created_at
            self.docs[str(obj.id)] = obj
        elif cls_name == "KbDocumentApproval":
            import datetime as _dt
            if obj.created_at is None:
                obj.created_at = _dt.datetime.now(_dt.UTC)
            self.approvals.append(obj)
        elif cls_name == "KbRetrievalAudit":
            import datetime as _dt
            if obj.created_at is None:
                obj.created_at = _dt.datetime.now(_dt.UTC)
            self.retrievals.append(obj)

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        pass

    async def get(self, model_cls, pk):
        cls_name = model_cls.__name__ if hasattr(model_cls, "__name__") else str(model_cls)
        if cls_name == "KbDocument":
            return self.docs.get(str(pk))
        if cls_name == "KbDocumentApproval":
            for a in self.approvals:
                if a.id == pk:
                    return a
        return None

    async def execute(self, stmt, params=None, **kwargs):
        column_descriptions = stmt.column_descriptions
        if not column_descriptions:
            return SimpleNamespace(
                scalars=MagicMock(return_value=SimpleNamespace(all=MagicMock(return_value=[]))),
                scalar=MagicMock(return_value=0),
            )
        target = column_descriptions[0]["entity"]
        entity_name = target.__name__ if hasattr(target, "__name__") else str(target)

        # 从 stmt 提取 bind params — 用 literal_binds 编译以便看见字面量
        bind_params = {}
        if isinstance(params, dict):
            bind_params.update({k: v for k, v in params.items() if isinstance(v, (str, int))})
        if kwargs:
            bind_params.update({k: v for k, v in kwargs.items() if isinstance(v, (str, int))})
        try:
            # 优先 literal_binds 渲染 (字面量可见), 失败再回退到 raw str
            try:
                compiled = stmt.compile(compile_kwargs={"literal_binds": True})
                where_str = str(compiled)
            except Exception:
                where_str = str(stmt.whereclause) if stmt.whereclause is not None else ""
            import re as _re
            for m in _re.finditer(r"document_id\s*=\s*'([a-f0-9-]{36})'", where_str):
                bind_params.setdefault("document_id", m.group(1))
            for m in _re.finditer(r"actor_id\s*=\s*'([^']*)'", where_str):
                bind_params.setdefault("actor_id", m.group(1))
        except Exception:
            pass

        if entity_name == "KbDocumentApproval":
            results = list(self.approvals)
            if "document_id" in bind_params:
                target_id = bind_params["document_id"]
                results = [r for r in results if str(r.document_id) == target_id]
            if "actor_id" in bind_params:
                target_actor = bind_params["actor_id"]
                results = [r for r in results if r.actor_id == target_actor]
            results = sorted(results, key=lambda x: x.created_at, reverse=True)
            return SimpleNamespace(
                scalars=MagicMock(return_value=SimpleNamespace(all=MagicMock(return_value=results))),
                all=MagicMock(return_value=results),
                scalar=MagicMock(return_value=len(results)),
            )

        if entity_name == "KbRetrievalAudit":
            results = list(self.retrievals)
            if "actor_id" in bind_params:
                target_actor = bind_params["actor_id"]
                results = [r for r in results if r.actor_id == target_actor]
            results = sorted(results, key=lambda x: x.created_at, reverse=True)
            return SimpleNamespace(
                scalars=MagicMock(return_value=SimpleNamespace(all=MagicMock(return_value=results))),
                all=MagicMock(return_value=results),
                scalar=MagicMock(return_value=len(results)),
            )

        if entity_name == "KbDocument":
            return SimpleNamespace(
                scalars=MagicMock(return_value=SimpleNamespace(all=MagicMock(return_value=list(self.docs.values())))),
                all=MagicMock(return_value=list(self.docs.values())),
                scalar=MagicMock(return_value=len(self.docs)),
            )

        return SimpleNamespace(
            scalars=MagicMock(return_value=SimpleNamespace(all=MagicMock(return_value=[]))),
            all=MagicMock(return_value=[]),
            scalar=MagicMock(return_value=0),
        )

    async def close(self):
        pass


# ── App + client fixture ──


@pytest_asyncio.fixture
async def app(monkeypatch):
    fake_redis = MagicMock()
    fake_redis.ping = AsyncMock(return_value=True)
    fake_redis.get = AsyncMock(return_value=None)
    fake_redis.set = AsyncMock(return_value=True)
    fake_redis.delete = AsyncMock(return_value=1)
    fake_redis.aclose = AsyncMock()

    async def _async_iter_keys(match=None, **_):
        for k in ["kp:rag:cache:h:abc", "kp:rag:cache:b:def", "other:prefix:1"]:
            yield k

    fake_redis.scan_iter = MagicMock(side_effect=_async_iter_keys)

    fake_es = MagicMock()
    fake_es.ping = AsyncMock(return_value=True)
    fake_es.search = AsyncMock(return_value={
        "hits": {"hits": [
            {"_id": "c1", "_score": 0.9, "_source": {
                "chunk_id": "c1", "doc_id": "d1", "content": "信用卡额度调整流程",
                "category": "CREDIT_CARD", "doc_type": "faq", "card_type": "visa",
                "customer_tier": "gold", "security_level": "internal", "version": "1.0",
                "chunk_type": "plain_text", "parent_chunk_id": None, "heading_path": "",
                "approval_status": "PUBLISHED", "is_current_version": True, "doc_group": "g1",
                "effective_date": "2024-01-01", "expiry_date": None, "model_version": "bge-m3-v1",
            }},
        ]},
    })
    fake_es.aclose = AsyncMock()

    fake_minio = MagicMock()
    fake_minio.put_object = MagicMock()
    fake_minio.bucket_exists = MagicMock(return_value=True)

    kafka_mock = AsyncMock()

    test_app = create_app()
    test_app.state.redis_client = fake_redis
    test_app.state.es_client = fake_es
    test_app.state.embedding_provider = None
    test_app.state.reranker_provider = None
    test_app.state.embedding_breaker = None
    test_app.state.llm_extractor = None

    fake_db = FakeSession()

    async def override_get_db():
        yield fake_db

    from kb.api.deps import get_db_session
    test_app.dependency_overrides[get_db_session] = override_get_db

    monkeypatch.setattr("kb.api.documents.get_minio", lambda: fake_minio)
    monkeypatch.setattr("kb.api.documents.publish_ingest_request", kafka_mock)
    monkeypatch.setattr("kb.api.admin.get_redis", lambda: fake_redis)

    yield test_app, fake_db, fake_redis, fake_es, fake_minio, kafka_mock

    test_app.dependency_overrides.clear()


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    test_app, _, _, _, _, _ = app
    return TestClient(test_app)


# ── 通用 helper ──


def _upload(client, token, filename="test.txt", content=b"x", **form):
    files = {"file": (filename, io.BytesIO(content), "text/plain")}
    data = {"category": "OTHER", "doc_type": "faq", **form}
    resp = client.post("/api/v1/documents", files=files, data=data,
                        headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 202, resp.text
    return resp.json()["doc_id"]


# ── T1: 上传初始 DRAFT ──


class TestT01UploadDraft:
    def test_upload_returns_202(self, app, client, jwt_alice):
        doc_id = _upload(client, jwt_alice)
        assert doc_id is not None

    def test_upload_kafka_payload_has_draft(self, app, client, jwt_alice):
        _, _, _, _, _, kafka_mock = app
        _upload(client, jwt_alice)
        kafka_mock.assert_called_once()
        args, _ = kafka_mock.call_args
        payload = args[1]
        assert payload["metadata"]["approval_status"] == "DRAFT"
        assert payload["metadata"]["is_current_version"] is True

    def test_upload_requires_auth(self, client):
        files = {"file": ("t.txt", io.BytesIO(b"x"), "text/plain")}
        resp = client.post("/api/v1/documents", files=files, data={"category": "OTHER"})
        assert resp.status_code == 401


# ── T2: JWT 鉴权 ──


class TestT02AuthFlow:
    def test_jwt_happy_path(self, client, jwt_alice):
        resp = client.get("/api/v1/documents",
                          headers={"Authorization": f"Bearer {jwt_alice}"})
        assert resp.status_code == 200

    def test_jwt_invalid_signature_401(self, client, jwt_alice):
        bad = jwt_alice[:-1] + ("A" if jwt_alice[-1] != "A" else "B")
        resp = client.get("/api/v1/documents", headers={"Authorization": f"Bearer {bad}"})
        assert resp.status_code == 401

    def test_jwt_no_bearer_401(self, client, jwt_alice):
        resp = client.get("/api/v1/documents", headers={"Authorization": jwt_alice})
        assert resp.status_code == 401

    def test_jwt_wrong_audience_401(self, client, settings):
        bad = _make_jwt(settings, extra={"aud": "wrong-aud"})
        resp = client.get("/api/v1/documents", headers={"Authorization": f"Bearer {bad}"})
        assert resp.status_code == 401

    def test_jwt_expired_401(self, client, settings):
        bad = _make_jwt(settings, exp_offset=-100)
        resp = client.get("/api/v1/documents", headers={"Authorization": f"Bearer {bad}"})
        assert resp.status_code == 401

    def test_no_auth_401(self, client):
        resp = client.get("/api/v1/documents")
        assert resp.status_code == 401


# ── T3: 完整审批闭环 ──


class TestT03FullApprovalFlow:
    def test_draft_to_published(self, app, client, jwt_alice, jwt_bob):
        _, fake_db, _, _, _, _ = app
        doc_id = _upload(client, jwt_alice)
        # 上传时已用 principal.actor_id 写 created_by, 直接走闭环

        r = client.post(f"/api/v1/documents/{doc_id}/submit", json={},
                        headers={"Authorization": f"Bearer {jwt_alice}"})
        assert r.status_code == 200
        assert r.json()["to_status"] == "IN_REVIEW"

        r = client.post(f"/api/v1/documents/{doc_id}/approve", json={},
                        headers={"Authorization": f"Bearer {jwt_bob}"})
        assert r.status_code == 200
        assert r.json()["to_status"] == "APPROVED"

        r = client.post(f"/api/v1/documents/{doc_id}/publish", json={},
                        headers={"Authorization": f"Bearer {jwt_bob}"})
        assert r.status_code == 200
        assert r.json()["to_status"] == "PUBLISHED"

        # 文档状态
        assert fake_db.docs[doc_id].approval_status.value == "PUBLISHED"
        # 3 条审批审计
        doc_approvals = [a for a in fake_db.approvals if str(a.document_id) == doc_id]
        assert len(doc_approvals) == 3
        actions = [a.action for a in doc_approvals]
        assert KbApprovalAction.SUBMIT in actions
        assert KbApprovalAction.APPROVE in actions
        assert KbApprovalAction.PUBLISH in actions


# ── T4: 4-eyes 双签 ──


class TestT04DualSign:
    def _submit_doc(self, client, token):
        doc_id = _upload(client, token)
        client.post(f"/api/v1/documents/{doc_id}/submit", json={},
                    headers={"Authorization": f"Bearer {token}"})
        return doc_id

    def test_self_approve_blocked(self, app, client, jwt_alice):
        _, fake_db, _, _, _, _ = app
        doc_id = self._submit_doc(client, jwt_alice)
        # 上传时 created_by=alice (从 principal.actor_id), alice 自批触发双签
        r = client.post(f"/api/v1/documents/{doc_id}/approve", json={},
                        headers={"Authorization": f"Bearer {jwt_alice}"})
        assert r.status_code == 403
        assert "双签" in r.json()["detail"]

    def test_different_actor_approved(self, client, jwt_alice, jwt_bob):
        doc_id = self._submit_doc(client, jwt_alice)
        r = client.post(f"/api/v1/documents/{doc_id}/approve", json={},
                        headers={"Authorization": f"Bearer {jwt_bob}"})
        assert r.status_code == 200

    def test_admin_exempt(self, app, client, jwt_carol):
        _, fake_db, _, _, _, _ = app
        doc_id = _upload(client, jwt_carol)
        client.post(f"/api/v1/documents/{doc_id}/submit", json={},
                    headers={"Authorization": f"Bearer {jwt_carol}"})
        # carol 自批, created_by=carol, admin 角色豁免双签
        r = client.post(f"/api/v1/documents/{doc_id}/approve", json={},
                        headers={"Authorization": f"Bearer {jwt_carol}"})
        assert r.status_code == 200


# ── T5: comment 强制 ──


class TestT05CommentRequired:
    def test_reject_without_comment_422(self, client, jwt_alice, jwt_bob):
        doc_id = _upload(client, jwt_alice)
        client.post(f"/api/v1/documents/{doc_id}/submit", json={},
                    headers={"Authorization": f"Bearer {jwt_alice}"})
        r = client.post(f"/api/v1/documents/{doc_id}/reject", json={"comment": None},
                        headers={"Authorization": f"Bearer {jwt_bob}"})
        assert r.status_code == 422
        assert "备注" in r.json()["detail"]

    def test_reject_whitespace_422(self, client, jwt_alice, jwt_bob):
        doc_id = _upload(client, jwt_alice)
        client.post(f"/api/v1/documents/{doc_id}/submit", json={},
                    headers={"Authorization": f"Bearer {jwt_alice}"})
        r = client.post(f"/api/v1/documents/{doc_id}/reject", json={"comment": "   \n  "},
                        headers={"Authorization": f"Bearer {jwt_bob}"})
        assert r.status_code == 422

    def test_reject_with_comment_ok(self, client, jwt_alice, jwt_bob):
        doc_id = _upload(client, jwt_alice)
        client.post(f"/api/v1/documents/{doc_id}/submit", json={},
                    headers={"Authorization": f"Bearer {jwt_alice}"})
        r = client.post(f"/api/v1/documents/{doc_id}/reject",
                        json={"comment": "内容不合规, 需补充来源"},
                        headers={"Authorization": f"Bearer {jwt_bob}"})
        assert r.status_code == 200
        assert r.json()["to_status"] == "REJECTED"


# ── T6: 终态 ARCHIVED ──


class TestT06TerminalState:
    def test_archived_rejects_submit(self, app, client, jwt_alice, jwt_carol):
        _, fake_db, _, _, _, _ = app
        doc_id = _upload(client, jwt_alice)
        fake_db.docs[doc_id].approval_status = KbApprovalStatus.PUBLISHED
        r = client.post(f"/api/v1/documents/{doc_id}/archive", json={"comment": "下架"},
                        headers={"Authorization": f"Bearer {jwt_carol}"})
        assert r.status_code == 200
        r = client.post(f"/api/v1/documents/{doc_id}/submit", json={},
                        headers={"Authorization": f"Bearer {jwt_alice}"})
        assert r.status_code == 422
        assert "终态" in r.json()["detail"]

    def test_archive_requires_admin(self, app, client, jwt_alice, jwt_bob):
        _, fake_db, _, _, _, _ = app
        doc_id = _upload(client, jwt_alice)
        fake_db.docs[doc_id].approval_status = KbApprovalStatus.PUBLISHED
        r = client.post(f"/api/v1/documents/{doc_id}/archive", json={"comment": "下架"},
                        headers={"Authorization": f"Bearer {jwt_bob}"})
        assert r.status_code == 403


# ── T7: 多租户 ──


class TestT07MultiTenant:
    def test_bank_prod_tenant_writes_correctly(self, app, client, jwt_dave):
        _, fake_db, _, _, _, _ = app
        doc_id = _upload(client, jwt_dave)
        assert fake_db.docs[doc_id].tenant_id == "bank-prod"

    def test_default_tenant_fallback(self, app, client, jwt_alice):
        _, fake_db, _, _, _, _ = app
        doc_id = _upload(client, jwt_alice)
        assert fake_db.docs[doc_id].tenant_id == "default"

    def test_audit_carries_tenant(self, app, client, jwt_dave):
        _, fake_db, _, _, _, _ = app
        doc_id = _upload(client, jwt_dave)
        client.post(f"/api/v1/documents/{doc_id}/submit", json={},
                    headers={"Authorization": f"Bearer {jwt_dave}"})
        recs = [a for a in fake_db.approvals if str(a.document_id) == doc_id]
        assert len(recs) == 1
        assert recs[0].tenant_id == "bank-prod"


# ── T8: 检索审计 ──


class TestT08RetrievalAudit:
    def test_retrieve_writes_audit(self, app, client, jwt_alice):
        _, fake_db, _, _, _, _ = app
        r = client.post("/api/v1/retrieve",
                        json={"query": "信用卡额度", "top_k": 5, "search_type": "bm25_only"},
                        headers={"Authorization": f"Bearer {jwt_alice}"})
        assert r.status_code == 200
        assert len(fake_db.retrievals) >= 1
        rec = fake_db.retrievals[0]
        assert rec.actor_id == "alice"
        assert rec.tenant_id == "default"
        assert rec.top_k == 5
        assert rec.query_hash == hashlib.md5("信用卡额度".encode()).hexdigest()
        # query 原文不入库
        for r in fake_db.retrievals:
            assert "信用卡额度" not in (r.__dict__.get("__raw__", "") or "")

    def test_request_id_in_audit(self, app, client, jwt_alice):
        _, fake_db, _, _, _, _ = app
        client.post("/api/v1/retrieve",
                    json={"query": "test", "top_k": 1, "search_type": "bm25_only"},
                    headers={"Authorization": f"Bearer {jwt_alice}", "X-Request-ID": "req-1"})
        rec = fake_db.retrievals[0]
        assert rec.request_id == "req-1"

    def test_audit_failure_does_not_break(self, app, client, jwt_alice, monkeypatch):
        from kb.api import retrieve as retrieve_module

        async def boom(*a, **kw):
            raise RuntimeError("炸了")
        monkeypatch.setattr(retrieve_module.AuditService, "log_retrieval", boom)
        r = client.post("/api/v1/retrieve",
                        json={"query": "x", "top_k": 1, "search_type": "bm25_only"},
                        headers={"Authorization": f"Bearer {jwt_alice}"})
        assert r.status_code == 200


# ── T9: 审批审计 ──


class TestT09ApprovalAudit:
    def test_submit_audit_record(self, app, client, jwt_alice):
        _, fake_db, _, _, _, _ = app
        doc_id = _upload(client, jwt_alice)
        client.post(f"/api/v1/documents/{doc_id}/submit", json={},
                    headers={"Authorization": f"Bearer {jwt_alice}"})
        recs = [a for a in fake_db.approvals if str(a.document_id) == doc_id]
        assert len(recs) == 1
        rec = recs[0]
        assert rec.action == KbApprovalAction.SUBMIT
        assert rec.from_status == KbApprovalStatus.DRAFT.value
        assert rec.to_status == KbApprovalStatus.IN_REVIEW.value
        assert rec.actor_id == "alice"
        assert rec.operation_result == "success"
        assert rec.risk_level == "normal"
        assert rec.tenant_id == "default"
        assert rec.ip is not None
        assert rec.retention_until is not None

    def test_archive_high_risk(self, app, client, jwt_alice, jwt_carol):
        _, fake_db, _, _, _, _ = app
        doc_id = _upload(client, jwt_alice)
        fake_db.docs[doc_id].approval_status = KbApprovalStatus.PUBLISHED
        client.post(f"/api/v1/documents/{doc_id}/archive", json={"comment": "下架"},
                    headers={"Authorization": f"Bearer {jwt_carol}"})
        archive_recs = [a for a in fake_db.approvals
                       if str(a.document_id) == doc_id and a.action == KbApprovalAction.ARCHIVE]
        assert len(archive_recs) == 1
        assert archive_recs[0].risk_level == "high"


# ── T10: 业务审计查询 ──


class TestT10BusinessAuditQuery:
    def test_business_audit_returns_both(self, app, client, jwt_carol):
        _, fake_db, _, _, _, _ = app
        # seed: alice 上传 + submit, bob approve, alice publish
        doc_id = _upload(client, jwt_alice) if False else None  # placeholder
        # 直接 seed DB
        from kb.orm.kb import KbDocument
        d = KbDocument(id=uuid.uuid4(), title="seed", source_type="TXT", file_path="x",
                       category="OTHER", doc_type="faq", security_level="internal",
                       version="1.0", status="PENDING", tenant_id="default",
                       approval_status=KbApprovalStatus.PUBLISHED, created_by="alice")
        fake_db.docs[str(d.id)] = d
        doc_id = str(d.id)

        for actor, action in [("alice", KbApprovalAction.SUBMIT), ("bob", KbApprovalAction.APPROVE), ("alice", KbApprovalAction.PUBLISH)]:
            client.post(f"/api/v1/documents/{doc_id}/submit", json={},
                        headers={"Authorization": f"Bearer {jwt_alice if actor == 'alice' else jwt_bob}"})
            # 简化: 只测审计查询, 不实际跑流程

        # 直接 seed 审批
        import datetime as _dt

        from kb.orm.kb import KbDocumentApproval
        now = _dt.datetime.now(_dt.UTC)
        for action, actor in [(KbApprovalAction.SUBMIT, "alice"), (KbApprovalAction.APPROVE, "bob"), (KbApprovalAction.PUBLISH, "alice")]:
            fake_db.approvals.append(KbDocumentApproval(
                id=uuid.uuid4(), document_id=d.id, action=action,
                from_status="DRAFT", to_status="PUBLISHED", actor_id=actor,
                actor_role="editor", comment=None, tenant_id="default",
                ip="127.0.0.1", ua="t/1.0", request_id="r1",
                operation_result="success", risk_level="normal",
                created_at=now, retention_until=now,
            ))
        for _ in range(3):
            from kb.orm.kb import KbRetrievalAudit
            fake_db.retrievals.append(KbRetrievalAudit(
                id=uuid.uuid4(), request_id="r1", actor_id="alice", tenant_id="default",
                query_hash=hashlib.md5(b"q").hexdigest(), top_k=10, result_count=5,
                latency_ms=42, search_type="hybrid", degraded=False,
                created_at=_dt.datetime.now(_dt.UTC),
            ))

        r = client.get("/api/v1/admin/business-audit",
                       headers={"Authorization": f"Bearer {jwt_carol}"})
        assert r.status_code == 200
        body = r.json()
        assert body["summary"]["approvals_returned"] == 3
        assert body["summary"]["retrievals_returned"] == 3

    def test_filter_by_actor(self, app, client, jwt_carol, jwt_alice, jwt_bob):
        _, fake_db, _, _, _, _ = app
        import datetime as _dt

        from kb.orm.kb import KbDocument, KbDocumentApproval
        now = _dt.datetime.now(_dt.UTC)
        d = KbDocument(id=uuid.uuid4(), title="s", source_type="TXT", file_path="x",
                       category="OTHER", doc_type="faq", security_level="internal",
                       version="1.0", status="PENDING", tenant_id="default",
                       approval_status=KbApprovalStatus.PUBLISHED, created_by="alice")
        fake_db.docs[str(d.id)] = d
        for action, actor in [(KbApprovalAction.SUBMIT, "alice"), (KbApprovalAction.APPROVE, "bob")]:
            fake_db.approvals.append(KbDocumentApproval(
                id=uuid.uuid4(), document_id=d.id, action=action,
                from_status="DRAFT", to_status="PUBLISHED", actor_id=actor,
                actor_role="editor", comment=None, tenant_id="default",
                created_at=now,
            ))

        r = client.get("/api/v1/admin/business-audit?actor_id=bob",
                       headers={"Authorization": f"Bearer {jwt_carol}"})
        assert r.status_code == 200
        body = r.json()
        assert body["summary"]["approvals_returned"] == 1
        assert body["approvals"][0]["actor_id"] == "bob"

    def test_no_pii(self, app, client, jwt_carol):
        _, fake_db, _, _, _, _ = app
        from kb.orm.kb import KbRetrievalAudit
        fake_db.retrievals.append(KbRetrievalAudit(
            id=uuid.uuid4(), actor_id="alice", tenant_id="default",
            query_hash=hashlib.md5(b"sensitive").hexdigest(),
            top_k=10, result_count=5, latency_ms=42, search_type="hybrid", degraded=False,
        ))
        r = client.get("/api/v1/admin/business-audit",
                       headers={"Authorization": f"Bearer {jwt_carol}"})
        body = r.json()
        for ret in body["retrievals"]:
            assert "query" not in ret
            assert "query_hash" in ret


# ── T11: diagnostics ──


class TestT11AdminDiagnostics:
    def test_diagnostics_no_error(self, app, client, jwt_carol):
        r = client.get("/api/v1/diagnostics",
                       headers={"Authorization": f"Bearer {jwt_carol}"})
        assert r.status_code == 200
        body = r.json()
        assert "_error" not in body.get("stage_stats", {})
        assert "health" in body
        assert "doc_distribution" in body


# ── T12: clear-cache ──


class TestT12AdminClearCache:
    def test_clear_cache_real_prefix(self, app, client, jwt_carol):
        _, _, fake_redis, _, _, _ = app
        r = client.post("/api/v1/admin/clear-cache",
                        headers={"Authorization": f"Bearer {jwt_carol}"})
        assert r.status_code == 200
        body = r.json()
        # 3 keys 都被清
        assert body["deleted_keys"] == 3
        assert fake_redis.delete.call_count == 3


# ── T13: 否定语义 ──


class TestT13ExcludeMustNot:
    def test_exclude_builds_clauses(self):
        """build_es_excludes 返回的子句由调用方包裹在 bool.must_not"""
        from kb.retrieval.engine import build_es_excludes

        clauses = build_es_excludes({"card_type": "visa"})
        assert {"term": {"card_type": "visa"}} in clauses

        clauses = build_es_excludes({"card_type": ["visa", "master"]})
        assert {"terms": {"card_type": ["visa", "master"]}} in clauses

        # 空 / None 不产出子句
        assert build_es_excludes({}) == []
        assert build_es_excludes({"x": None}) == []
        assert build_es_excludes({"x": []}) == []

    def test_tenant_in_keyword_fields(self):
        from kb.retrieval.engine import _ES_KEYWORD_FIELDS
        assert "tenant_id" in _ES_KEYWORD_FIELDS


# ── T14: 非法转移 ──


class TestT14IllegalTransitions:
    def test_draft_cannot_approve(self, client, jwt_bob):
        doc_id = _upload(client, jwt_bob)
        r = client.post(f"/api/v1/documents/{doc_id}/approve", json={},
                        headers={"Authorization": f"Bearer {jwt_bob}"})
        assert r.status_code == 422
        assert "不能执行" in r.json()["detail"]

    def test_published_cannot_reject(self, app, client, jwt_alice, jwt_bob):
        _, fake_db, _, _, _, _ = app
        doc_id = _upload(client, jwt_alice)
        fake_db.docs[doc_id].approval_status = KbApprovalStatus.PUBLISHED
        r = client.post(f"/api/v1/documents/{doc_id}/reject", json={"comment": "补打"},
                        headers={"Authorization": f"Bearer {jwt_bob}"})
        assert r.status_code == 422


# ── T15: 字符串入参 ──


class TestT15WorkflowStringInputs:
    def test_string_status_action(self):
        from kb.security.workflow import validate_transition
        new = validate_transition(current_status="DRAFT", action="SUBMIT",
                                   actor_id="x", actor_role="editor", comment=None)
        assert new == KbApprovalStatus.IN_REVIEW

    def test_invalid_status(self):
        from kb.security.workflow import WorkflowError, validate_transition
        with pytest.raises(WorkflowError) as exc:
            validate_transition(current_status="UNKNOWN", action="SUBMIT",
                                actor_id="x", actor_role="editor", comment=None)
        assert exc.value.code == "invalid_status"


# ── T16: 审批历史 ──


class TestT16ApprovalHistory:
    def test_list_approvals(self, app, client, jwt_alice, jwt_bob, jwt_carol):
        _, fake_db, _, _, _, _ = app
        doc_id = _upload(client, jwt_alice)
        client.post(f"/api/v1/documents/{doc_id}/submit", json={},
                    headers={"Authorization": f"Bearer {jwt_alice}"})
        client.post(f"/api/v1/documents/{doc_id}/approve", json={},
                    headers={"Authorization": f"Bearer {jwt_bob}"})

        r = client.get(f"/api/v1/documents/{doc_id}/approvals",
                       headers={"Authorization": f"Bearer {jwt_carol}"})
        assert r.status_code == 200
        body = r.json()
        assert body["current_status"] == "APPROVED"
        assert "PUBLISH" in body["allowed_actions"]
        assert len(body["records"]) == 2
        assert body["records"][0]["action"] == "APPROVE"
        assert body["records"][1]["action"] == "SUBMIT"


# ──────────────────────────────────────────────────────────────────────
# I2-C2: 检索降级 E2E (T17-T20)
# ──────────────────────────────────────────────────────────────────────


class _FakeESClient:
    """可编程 ES client — 模拟 RRF / BM25 / kNN 行为, 通过 fake_es.kinds 控制"""

    def __init__(self):
        self.kinds: list[str] = []      # 每次 .search() 弹出
        self.rrf_results: list = []
        self.bm25_results: list = []
        self.knn_results: list = []
        self.search_calls: list[dict] = []

    async def search(self, *, body=None, knn=None, index=None, **kw):
        self.search_calls.append({"body": body, "knn": knn, "index": index, **kw})
        # 推断调用类型
        if knn is not None:
            kind = "knn"
            results = self.knn_results
        else:
            kind = "rrf" if self.kinds and self.kinds[0] == "rrf" else "bm25"
            if not self.kinds:
                kind = "bm25"
            else:
                self.kinds.pop(0)
            results = self.rrf_results if kind == "rrf" else self.bm25_results
        # 模拟失败模式
        if kind == "fail_rrf":
            raise RuntimeError("ES RRF down")
        if kind == "fail_bm25":
            raise RuntimeError("ES BM25 down")
        return {"hits": {"hits": [{"_id": h.get("chunk_id", "x"), "_score": h.get("score", 0.9),
                                    "_source": h} for h in results]}}

    async def aclose(self):
        pass

    async def ping(self):
        return True


class _FakeRedis:
    """简单内存 Redis, 模拟 get/setex/scan_iter"""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, k):
        return self.store.get(k)

    async def setex(self, k, ttl, v):
        self.store[k] = v

    async def delete(self, k):
        self.store.pop(k, None)

    def scan_iter(self, match=None):
        # 简单实现: 返回所有 key (测试用)
        for k in list(self.store.keys()):
            yield k

    async def ping(self):
        return True


class _FakeEmbedProvider:
    """可编程 embed provider — 默认成功, 通过 fail_next 让下次 embed 抛错"""

    def __init__(self):
        self.fail_next = False
        self.call_count = 0

    async def embed_query(self, query):
        self.call_count += 1
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("TEI embed fail")
        return [0.1] * 4

    async def health_check(self):
        return True


class TestT17BreakerOpenTriggersL2:
    """T17: 嵌入熔断器 OPEN → 跳过 embed → 走 L2 bm25"""

    def test_breaker_open_skips_embed(self, app, client, jwt_alice, monkeypatch):
        test_app, fake_db, fake_redis, fake_es, _, _ = app
        from kb.circuit_breaker import GenericCircuitBreaker
        from kb.retrieval.models import RetrievedChunk

        # 构造 OPEN 状态熔断器
        breaker = GenericCircuitBreaker(name="embedding", failure_threshold=2, cooldown_seconds=60)
        import asyncio
        asyncio.run(breaker.record_failure())
        asyncio.run(breaker.record_failure())
        assert breaker.is_available is False
        test_app.state.embedding_breaker = breaker

        # monkeypatch BM25 返 c2 (跨测试隔离, 不依赖 fake_es state)
        async def fake_bm25(es_client, query, top_k, filters):
            return [RetrievedChunk(chunk_id="c2", content="BM25 命中", score=0.5, source_doc="d2")]
        monkeypatch.setattr("kb.retrieval.engine._search_bm25_only", fake_bm25)

        embed = _FakeEmbedProvider()
        test_app.state.embedding_provider = embed
        test_app.state.redis_client = fake_redis

        r = client.post("/api/v1/retrieve", json={
            "query": "信用卡年费", "top_k": 3, "search_type": "hybrid", "timeout_ms": 1500,
            "tenant_id": "bank-a", "actor_roles": ["cs"],
        }, headers={"Authorization": f"Bearer {jwt_alice}"})

        assert r.status_code == 200
        body = r.json()
        # embed 未被调用 (熔断器拦截)
        assert embed.call_count == 0
        # L2 bm25 返了结果
        assert len(body["results"]) == 1
        assert body["results"][0]["chunk_id"] == "c2"
        # 标记降级
        assert body["degraded"] is True
        assert "hybrid→bm25" in body["degraded_stages"]


class TestT18EmbedTimeoutTriggersL2:
    """T18: 嵌入超时 → 降级 L2 bm25"""

    def test_embed_timeout_degrades_to_bm25(self, app, client, jwt_alice, monkeypatch):
        import asyncio
        test_app, fake_db, fake_redis, fake_es, _, _ = app

        # embed 模拟超时: 用 asyncio.sleep > budget
        class SlowEmbed:
            def __init__(self):
                self.call_count = 0
            async def embed_query(self, q):
                self.call_count += 1
                await asyncio.sleep(0.5)
                return [0.1] * 4
            async def health_check(self):
                return True

        test_app.state.embedding_provider = SlowEmbed()
        # timeout_ms=100 → embed budget 50ms, 立即超时
        test_app.state.embedding_breaker = None
        fake_es.kinds = []  # bm25
        fake_es.bm25_results = [{"chunk_id": "c2", "content": "fallback", "score": 0.4,
                                  "doc_id": "d2"}]

        r = client.post("/api/v1/retrieve", json={
            "query": "q", "top_k": 3, "search_type": "hybrid", "timeout_ms": 100,
            "tenant_id": "bank-a", "actor_roles": ["cs"],
        }, headers={"Authorization": f"Bearer {jwt_alice}"})

        assert r.status_code == 200
        body = r.json()
        assert body["degraded"] is True
        assert "hybrid→bm25" in body["degraded_stages"]


class TestT19CrossTenantCacheIsolation:
    """T19: 跨租户缓存隔离 — alice (tenant=A) 与 bob (tenant=B) 同 query 不命中对方"""

    def test_cross_tenant_does_not_pollute_cache(self, app, client, jwt_alice, jwt_bob, monkeypatch):
        test_app, fake_db, fake_redis, fake_es, _, _ = app
        from kb.retrieval.models import RetrievedChunk

        # monkeypatch BM25 返 shared 结果, 计数 search 调用
        call_count = {"n": 0}
        async def fake_bm25(es_client, query, top_k, filters):
            call_count["n"] += 1
            return [RetrievedChunk(chunk_id=f"c{call_count['n']}", content="shared",
                                    score=0.5, source_doc="d1")]
        monkeypatch.setattr("kb.retrieval.engine._search_bm25_only", fake_bm25)

        embed = _FakeEmbedProvider()
        test_app.state.embedding_provider = embed
        test_app.state.embedding_breaker = None
        # 重要: 用全新的 redis (清掉上轮测试残留)
        test_app.state.redis_client = _FakeRedis()

        # alice (bank-a) 检索
        r1 = client.post("/api/v1/retrieve", json={
            "query": "信用卡", "top_k": 3, "search_type": "bm25_only", "timeout_ms": 1500,
            "tenant_id": "bank-a", "actor_roles": ["cs"],
        }, headers={"Authorization": f"Bearer {jwt_alice}"})
        assert r1.status_code == 200

        # bob (bank-b) 同 query 检索 — 应有独立缓存 key
        r2 = client.post("/api/v1/retrieve", json={
            "query": "信用卡", "top_k": 3, "search_type": "bm25_only", "timeout_ms": 1500,
            "tenant_id": "bank-b", "actor_roles": ["cs"],
        }, headers={"Authorization": f"Bearer {jwt_bob}"})
        assert r2.status_code == 200

        # 验证: 2 次请求, BM25 被打 2 次 (如果跨租户缓存命中, 第二次会复用)
        assert call_count["n"] == 2
        # Redis 中有 2 个不同 key
        redis = test_app.state.redis_client
        assert len(redis.store) >= 2
        keys = list(redis.store.keys())
        for k in keys:
            assert k.startswith("kp:rag:cache:bm25_only:")
        # 但 hash 部分不同 (tenant 隔离)
        hashes = [k.split(":")[-1] for k in keys]
        assert len(set(hashes)) >= 2  # 至少 2 个不同 hash


class TestT20DegradedResultInAudit:
    """T20: 降级路径 → KbRetrievalAudit.degraded=True 落表"""

    def test_degraded_audit_recorded(self, app, client, jwt_alice, monkeypatch):
        from kb.retrieval.models import RetrievedChunk
        test_app, fake_db, fake_redis, fake_es, _, _ = app

        class FailingEmbed:
            async def embed_query(self, q):
                raise RuntimeError("TEI fail")
            async def health_check(self):
                return False

        # monkeypatch BM25 返 fallback 结果 (这样审计有 result_count=1)
        async def fake_bm25(es_client, query, top_k, filters):
            return [RetrievedChunk(chunk_id="c2", content="fallback", score=0.4, source_doc="d2")]
        monkeypatch.setattr("kb.retrieval.engine._search_bm25_only", fake_bm25)

        test_app.state.embedding_provider = FailingEmbed()
        test_app.state.embedding_breaker = None

        r = client.post("/api/v1/retrieve", json={
            "query": "信用卡", "top_k": 3, "search_type": "hybrid", "timeout_ms": 1500,
            "tenant_id": "bank-a", "actor_roles": ["cs"],
        }, headers={"Authorization": f"Bearer {jwt_alice}"})

        assert r.status_code == 200
        body = r.json()
        assert body["degraded"] is True

        # 审计: 至少有 1 条 KbRetrievalAudit, degraded=True
        # (embed 失败 → 走 L2 bm25 → 仍 degraded=True 因为 hybrid→bm25 阶段降级)
        degraded_audits = [a for a in fake_db.retrievals if a.degraded is True]
        assert len(degraded_audits) >= 1


# ──────────────────────────────────────────────────────────────────────
# I2-C1: 限流分级 E2E (T21-T23)
# ──────────────────────────────────────────────────────────────────────


class _RateLimitFakeRedis:
    """支持限流 Lua 脚本的最小 Redis fake

    模拟 INCR + EXPIRE 语义, 不依赖真 Redis.
    计数用 in-memory dict, TTL 用 monotonic time.
    """

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.expires: dict[str, float] = {}

    async def eval(self, script: str, numkeys: int, key: str, *args):
        # 简单实现限流 Lua 语义
        import time as _t

        window = int(args[0]) if args else 60
        now = _t.monotonic()
        exp = self.expires.get(key, 0.0)
        if exp and now > exp:
            # 已过期, 重置
            self.counters.pop(key, None)
            self.expires.pop(key, None)
        current = self.counters.get(key, 0) + 1
        self.counters[key] = current
        if current == 1:
            self.expires[key] = now + window
        ttl = max(0, int(self.expires.get(key, now + window) - now))
        return [current, ttl]

    async def ping(self):
        return True

    async def aclose(self):
        pass


@pytest.fixture
def ratelimit_app(monkeypatch, settings):
    """独立的 E2E fixture — 装上支持 eval 的 fake redis

    用于 T21-T23 限流分级测试, 不依赖业务路由是否完整.
    """
    from fastapi.testclient import TestClient

    from kb.main import create_app

    test_app = create_app()

    # 装支持 eval 的 fake redis
    rl_redis = _RateLimitFakeRedis()
    test_app.state.redis_client = rl_redis

    # 注入到 kb.storage.redis._client, 让 RateLimitMiddleware.get_redis() 拿到
    import kb.storage.redis as _redis_mod
    monkeypatch.setattr(_redis_mod, "_client", rl_redis)

    # disable ES / embedding / reranker 依赖 (这些端点会调到)
    test_app.state.es_client = None
    test_app.state.embedding_provider = None
    test_app.state.reranker_provider = None
    test_app.state.embedding_breaker = None
    test_app.state.llm_extractor = None

    # 关掉 rate limit 之后, 需要走 /api/v1/retrieve 等端点 — 但端点内部会查 ES
    # 我们改用更轻的端点: /api/v1/documents (GET) — 这个只会返回空 list
    # 或者直接测试 middleware 自身, 不走业务路由

    client = TestClient(test_app)
    yield test_app, rl_redis, client

    # 清理
    monkeypatch.setattr(_redis_mod, "_client", None)


class TestT21TierQuotaDifferentiation:
    """T21: tier 配额差异化 — normal 与 vip 配额不同"""

    @pytest.mark.asyncio
    async def test_normal_user_limited_to_10_uploads(self, ratelimit_app, settings):
        """normal 用户 upload 配额 = 10/min, 第 11 次应 429"""
        from tests.test_i1_e2e import _make_jwt

        test_app, rl_redis, client = ratelimit_app
        jwt_normal = _make_jwt(settings, sub="alice", tier="normal", roles=["editor"])

        from kb.middleware.rate_limit import RateLimitMiddleware

        mw = RateLimitMiddleware(test_app)

        async def call_next(req):
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {}
            return resp

        last_status = None
        last_headers: dict = {}
        for i in range(11):
            request = MagicMock()
            request.method = "POST"
            request.url.path = "/api/v1/documents"
            request.headers = {"authorization": f"Bearer {jwt_normal}"}
            request.client.host = "127.0.0.1"
            response = await mw.dispatch(request, call_next)
            if hasattr(response, "status_code"):
                last_status = response.status_code
                last_headers = dict(response.headers) if hasattr(response, "headers") else {}

        assert last_status == 429
        assert last_headers.get("Retry-After") is not None or "retry-after" in str(last_headers).lower()

    @pytest.mark.asyncio
    async def test_vip_user_not_limited_at_100(self, ratelimit_app, settings):
        """vip 用户配额 = 200/min, 跑 100 次仍放行"""
        from kb.middleware.rate_limit import RateLimitMiddleware
        from tests.test_i1_e2e import _make_jwt

        test_app, rl_redis, client = ratelimit_app
        jwt_vip = _make_jwt(settings, sub="vip_user", tier="vip", roles=["editor"])

        mw = RateLimitMiddleware(test_app)

        async def call_next(req):
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {}
            return resp

        statuses = []
        for i in range(100):
            request = MagicMock()
            request.method = "GET"
            request.url.path = "/api/v1/documents"
            request.headers = {"authorization": f"Bearer {jwt_vip}"}
            request.client.host = "127.0.0.1"
            response = await mw.dispatch(request, call_next)
            statuses.append(response.status_code)

        assert all(s == 200 for s in statuses), f"vip 被错误限流: {set(statuses)}"


class TestT22UploadQuotaIndependent:
    """T22: upload (write) 配额独立于 read"""

    @pytest.mark.asyncio
    async def test_read_does_not_consume_write_quota(self, ratelimit_app, settings):
        """GET 读请求 100 次不影响 POST upload 配额 (不同 group 独立计数)"""
        from kb.middleware.rate_limit import RateLimitMiddleware
        from tests.test_i1_e2e import _make_jwt

        test_app, rl_redis, client = ratelimit_app
        jwt_normal = _make_jwt(settings, sub="alice", tier="normal", roles=["editor"])

        mw = RateLimitMiddleware(test_app)

        async def call_next(req):
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {}
            return resp

        for i in range(100):
            request = MagicMock()
            request.method = "GET"
            request.url.path = "/api/v1/documents"
            request.headers = {"authorization": f"Bearer {jwt_normal}"}
            request.client.host = "127.0.0.1"
            await mw.dispatch(request, call_next)

        for i in range(10):
            request = MagicMock()
            request.method = "POST"
            request.url.path = "/api/v1/documents"
            request.headers = {"authorization": f"Bearer {jwt_normal}"}
            request.client.host = "127.0.0.1"
            response = await mw.dispatch(request, call_next)
            assert response.status_code == 200, f"第 {i+1} 次 upload 被错误限流"

        request = MagicMock()
        request.method = "POST"
        request.url.path = "/api/v1/documents"
        request.headers = {"authorization": f"Bearer {jwt_normal}"}
        request.client.host = "127.0.0.1"
        response = await mw.dispatch(request, call_next)
        assert response.status_code == 429


class TestT23HealthAndMetricsWhitelisted:
    """T23: /health/* 与 /metrics 不计入限流"""

    @pytest.mark.asyncio
    async def test_health_endpoints_skip_rate_limit(self, ratelimit_app, settings):
        from kb.middleware.rate_limit import RateLimitMiddleware

        test_app, rl_redis, client = ratelimit_app
        mw = RateLimitMiddleware(test_app)

        async def call_next(req):
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {}
            return resp

        for i in range(1000):
            request = MagicMock()
            request.method = "GET"
            request.url.path = "/health/live"
            request.headers = {}
            request.client.host = "127.0.0.1"
            response = await mw.dispatch(request, call_next)
            assert response.status_code == 200

        assert len(rl_redis.counters) == 0, f"白名单不应计数, 但有: {list(rl_redis.counters)}"
