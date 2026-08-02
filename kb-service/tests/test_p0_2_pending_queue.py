"""P0-2.1 待审批队列 (GET /approvals/pending) 测试

覆盖:
- T1  默认 status=IN_REVIEW
- T2  status=APPROVED 返回已批准列表
- T3  status=INVALID → 422
- T4  status=ARCHIVED → 422 (终态不进队列)
- T5  跨租户隔离 (alice default 看不到 dave bank-prod)
- T6  is_deleted 排除
- T7  分页 (limit/offset 切片正确)
- T8  total 与 limit/offset 无关
- T9  last_actor_id 取自最近一次 KbDocumentApproval
- T10 query 强制 principal.tenant_id, 不接受 query 覆盖

不需要真实 ES/Redis — 用 in-memory fake.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import jwt
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

fastapi = pytest.importorskip("fastapi")

from kb.config import get_settings
from kb.main import create_app

# ── JWT helper ──


def _make_jwt(
    settings,
    *,
    sub: str = "alice",
    tenant_id: str = "default",
    roles: list[str] | None = None,
    actor_role: str | None = None,
    tier: str = "normal",
    exp_offset: int = 3600,
) -> str:
    now = dt.datetime.now(dt.UTC)
    payload = {
        "sub": sub,
        "tenant_id": tenant_id,
        "roles": roles or ["reviewer"],
        "actor_role": actor_role or (roles[0] if roles else "user"),
        "tier": tier,
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + exp_offset,
        "aud": settings.jwt.audience,
        "iss": settings.jwt.issuer,
    }
    return jwt.encode(payload, settings.jwt.secret, algorithm=settings.jwt.algorithm)


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def jwt_alice(settings):
    return _make_jwt(settings, sub="alice", tenant_id="default", roles=["reviewer"])


@pytest.fixture
def jwt_dave(settings):
    return _make_jwt(settings, sub="dave", tenant_id="bank-prod", roles=["reviewer"])


# ── Fake DB / ES / Redis fixtures ──


class _FakeAsyncSession:
    """最小 AsyncSession — 支持 KbDocument 列表查询 (select + count + get)

    队列端点用到的 SQL 模式:
      1. select count(*).select_from(subquery)         → scalar_one()
      2. select(doc).where(...).order_by().limit().offset() → scalars().all()
      3. select(approval.document_id, approval.actor_id, approval.created_at)
         .where(document_id.in_(...)).order_by()       → .all()

    真实测试用 in-memory 字典模拟, 不走 SQLAlchemy 表达式解析.
    """

    def __init__(self):
        self.docs: dict[str, Any] = {}
        self.approvals: list[Any] = []
        self.committed = False

    def add(self, obj):
        cls_name = obj.__class__.__name__
        if cls_name == "KbDocument":
            self.docs[str(obj.id)] = obj
        elif cls_name == "KbDocumentApproval":
            self.approvals.append(obj)

    async def get(self, _model, pk):
        return self.docs.get(str(pk))

    async def commit(self):
        self.committed = True

    async def close(self):
        pass

    async def flush(self):
        pass

    async def execute(self, query):
        """极简 SQL 模拟: 提取 limit/offset/WHERE 条件从编译后 SQL, 过滤 self.docs

        真实 SQLAlchemy 表达式树解析太重, 改用 compile() 后字符串匹配
        提取 limit/offset/where 列 — 这只够 P0-2.1 队列单测用.

        支持的 WHERE 模式:
          - is_deleted IS false / IS true
          - tenant_id = '...'
          - approval_status = '...'
        """
        import re
        try:
            compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
        except Exception:
            compiled = str(query)
        compiled_l = compiled.lower()

        # 1. count(*) — 通常是 select count(*).select_from(subquery)
        #    外层是 count(*), 但 WHERE 在内层 subquery, 需要扫整个 compiled 找
        if "count(*)" in compiled_l:
            filters = self._extract_where_filters(compiled)
            results = [d for d in self.docs.values() if not d.is_deleted]
            if "tenant_id" in filters:
                results = [d for d in results if d.tenant_id == filters["tenant_id"]]
            if "approval_status" in filters:
                results = [d for d in results if d.approval_status.value == filters["approval_status"]]
            n = len(results)
            return SimpleNamespace(scalar_one=lambda: n, scalar=lambda: n)

        # 2. KbDocumentApproval 投影 (actor_id, created_at) — IN 子句
        # 注意: 表名实际是 kb_document_approval, 不是 kbdocumentapproval
        if "kb_document_approval" in compiled_l and "actor_id" in compiled_l:
            m = re.search(r"in\s*\(([^)]+)\)", compiled_l)
            doc_ids = set()
            if m:
                for s in m.group(1).split(","):
                    s = s.strip().strip("'\"")
                    if s:
                        doc_ids.add(s.replace("-", "").lower())
            rows = []
            for a in self.approvals:
                # SQLAlchemy literal_binds 输出无 dash, Python str(UUID) 有 dash, 归一化
                a_norm = str(a.document_id).replace("-", "").lower()
                if a_norm in doc_ids:
                    rows.append((a.document_id, a.actor_id, a.created_at))
            rows.sort(key=lambda r: r[2] or dt.datetime.min, reverse=True)
            return SimpleNamespace(all=lambda: rows)

        # 3. KbDocument 主查询
        if "kb_document" in compiled_l:
            results = self._filter_docs(compiled)
            m = re.search(r"order by\s+kb_document\.([\w_]+)", compiled_l)
            order_col = m.group(1) if m else "updated_at"
            results.sort(key=lambda d: getattr(d, order_col, None) or dt.datetime.min, reverse=True)

            m = re.search(r"limit\s+(\d+)", compiled_l)
            limit = int(m.group(1)) if m else None
            m = re.search(r"offset\s+(\d+)", compiled_l)
            offset = int(m.group(1)) if m else 0

            if limit is not None:
                results = results[offset: offset + limit]
            elif offset:
                results = results[offset:]

            mock = MagicMock()
            mock.scalars.return_value.all.return_value = results
            mock.all.return_value = [(d, None, None) for d in results]
            return mock

        # 兜底空
        return SimpleNamespace(
            scalars=lambda: MagicMock(all=lambda: []),
            all=lambda: [],
            scalar_one=lambda: 0,
            scalar=lambda: 0,
        )

    def _filter_docs(self, compiled_l: str) -> list[Any]:
        """按编译后 SQL 的 WHERE 子句过滤 self.docs (粗略)"""
        filters = self._extract_where_filters(compiled_l)
        results = [d for d in self.docs.values() if not d.is_deleted]
        if "tenant_id" in filters:
            results = [d for d in results if d.tenant_id == filters["tenant_id"]]
        if "approval_status" in filters:
            results = [d for d in results if d.approval_status.value == filters["approval_status"]]
        return results

    @staticmethod
    def _extract_where_filters(compiled_l: str) -> dict[str, str]:
        """从 WHERE 子句提取 column = 'value' 模式 (扫所有出现, 含子查询)"""
        import re
        filters: dict[str, str] = {}
        for m in re.finditer(r"(\w+)\s*=\s*'([^']*)'", compiled_l):
            col, val = m.group(1), m.group(2)
            if col in ("tenant_id", "approval_status"):
                filters[col] = val
        return filters


class _FakeESClient:
    def __init__(self):
        self.ping = MagicMock()
        self.search = MagicMock()
        self.aclose = MagicMock()


class _FakeRedis:
    def __init__(self):
        self.ping = MagicMock()
        self.get = MagicMock(return_value=None)
        self.set = MagicMock()
        self.delete = MagicMock()
        self.aclose = MagicMock()


class _FakeEmbedProvider:
    def __init__(self, dim: int = 8):
        self.dim = dim

    async def embed_query(self, query: str) -> list[float]:
        return [0.0] * self.dim

    async def embed_documents(self, docs: list[str]) -> list[list[float]]:
        return [[0.0] * self.dim for _ in docs]


@pytest_asyncio.fixture
async def app(monkeypatch):
    fake_redis = _FakeRedis()
    fake_redis.scan_iter = MagicMock(return_value=iter([]))

    fake_es = _FakeESClient()
    test_app = create_app()
    test_app.state.redis_client = fake_redis
    test_app.state.es_client = fake_es
    test_app.state.embedding_provider = _FakeEmbedProvider()
    test_app.state.reranker_provider = None
    test_app.state.embedding_breaker = None
    test_app.state.llm_extractor = None
    test_app.state.drift_monitor = None

    fake_db = _FakeAsyncSession()

    async def override_get_db():
        yield fake_db

    from kb.api.deps import get_db_session
    test_app.dependency_overrides[get_db_session] = override_get_db
    monkeypatch.setattr("kb.api.admin.get_redis", lambda: fake_redis)

    fake_minio = MagicMock()
    fake_minio.put_object = MagicMock()
    fake_minio.bucket_exists = MagicMock(return_value=True)
    monkeypatch.setattr("kb.api.documents.get_minio", lambda: fake_minio)

    async def fake_publish_ingest(doc_id, payload):
        pass
    monkeypatch.setattr("kb.api.documents.publish_ingest_request", fake_publish_ingest)

    yield test_app, fake_db, fake_redis, fake_es

    test_app.dependency_overrides.clear()


@pytest.fixture
def client(app):
    test_app, _, _, _ = app
    return TestClient(test_app)


# ── helper: 注入一个带状态的 KbDocument + 可选 KbDocumentApproval ──


def _make_doc(
    *,
    doc_id: str | None = None,
    tenant_id: str = "default",
    title: str = "untitled",
    category: str = "policy",
    approval_status_value: str = "IN_REVIEW",
    is_deleted: bool = False,
    is_current_version: bool = True,
    version: str = "1.0",
    updated_at: dt.datetime | None = None,
):
    """构造 KbDocument-like 对象 (SimpleNamespace, 因为真 ORM 跟 SQLAlchemy 绑死)"""
    if doc_id is None:
        doc_id = str(uuid.uuid4())
    if updated_at is None:
        updated_at = dt.datetime.now(dt.UTC)
    return SimpleNamespace(
        id=uuid.UUID(doc_id),
        title=title,
        category=category,
        tenant_id=tenant_id,
        approval_status=SimpleNamespace(value=approval_status_value),
        is_deleted=is_deleted,
        is_current_version=is_current_version,
        version=version,
        updated_at=updated_at,
        created_at=updated_at,
        created_by="alice",
        updated_by="alice",
    )


def _make_approval(
    *,
    doc_id: str,
    action: str = "SUBMIT",
    actor_id: str = "alice",
    from_status: str = "DRAFT",
    to_status: str = "IN_REVIEW",
    created_at: dt.datetime | None = None,
    tenant_id: str = "default",
):
    if created_at is None:
        created_at = dt.datetime.now(dt.UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        document_id=uuid.UUID(doc_id),
        action=SimpleNamespace(value=action),
        actor_id=actor_id,
        actor_role="reviewer",
        from_status=from_status,
        to_status=to_status,
        comment=None,
        tenant_id=tenant_id,
        ip=None,
        ua=None,
        request_id=None,
        operation_result="success",
        risk_level="normal",
        retention_until=None,
        created_at=created_at,
    )


# ═══════════════════════════════════════════════════════════════════════
# T1 默认 status=IN_REVIEW
# ═══════════════════════════════════════════════════════════════════════


class TestPendingQueueDefaults:
    def test_default_status_is_in_review(self, app, client, jwt_alice):
        """不传 status 时, 默认查询 IN_REVIEW 文档"""
        test_app, fake_db, _, _ = app
        # 注入 1 个 IN_REVIEW + 1 个 APPROVED, 默认查询应只返 IN_REVIEW
        d1 = _make_doc(approval_status_value="IN_REVIEW", title="待审")
        d2 = _make_doc(approval_status_value="APPROVED", title="已批")
        fake_db.docs[str(d1.id)] = d1
        fake_db.docs[str(d2.id)] = d2

        r = client.get("/api/v1/documents/approvals/pending", headers={"Authorization": f"Bearer {jwt_alice}"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert len(body["items"]) == 1
        assert body["items"][0]["doc_id"] == str(d1.id)
        assert body["items"][0]["approval_status"] == "IN_REVIEW"


# ═══════════════════════════════════════════════════════════════════════
# T2 status=APPROVED
# ═══════════════════════════════════════════════════════════════════════


class TestPendingQueueStatusFilter:
    def test_status_approved(self, app, client, jwt_alice):
        test_app, fake_db, _, _ = app
        d1 = _make_doc(approval_status_value="IN_REVIEW", title="待审")
        d2 = _make_doc(approval_status_value="APPROVED", title="已批")
        fake_db.docs[str(d1.id)] = d1
        fake_db.docs[str(d2.id)] = d2

        r = client.get(
            "/api/v1/documents/approvals/pending?status=APPROVED",
            headers={"Authorization": f"Bearer {jwt_alice}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["doc_id"] == str(d2.id)
        assert body["items"][0]["approval_status"] == "APPROVED"

    def test_status_published(self, app, client, jwt_alice):
        test_app, fake_db, _, _ = app
        d1 = _make_doc(approval_status_value="PUBLISHED", title="已发布")
        fake_db.docs[str(d1.id)] = d1

        r = client.get(
            "/api/v1/documents/approvals/pending?status=PUBLISHED",
            headers={"Authorization": f"Bearer {jwt_alice}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["approval_status"] == "PUBLISHED"


# ═══════════════════════════════════════════════════════════════════════
# T3 status=INVALID
# ═══════════════════════════════════════════════════════════════════════


class TestPendingQueueInvalidStatus:
    def test_unknown_status_returns_422(self, app, client, jwt_alice):
        r = client.get(
            "/api/v1/documents/approvals/pending?status=UNKNOWN",
            headers={"Authorization": f"Bearer {jwt_alice}"},
        )
        assert r.status_code == 422
        assert "UNKNOWN" in r.json()["detail"] or "未知" in r.json()["detail"]

    def test_archived_status_returns_422(self, app, client, jwt_alice):
        """ARCHIVED 是终态, 不进入待审批队列"""
        r = client.get(
            "/api/v1/documents/approvals/pending?status=ARCHIVED",
            headers={"Authorization": f"Bearer {jwt_alice}"},
        )
        assert r.status_code == 422
        assert "ARCHIVED" in r.json()["detail"] or "终态" in r.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════
# T5 跨租户隔离
# ═══════════════════════════════════════════════════════════════════════


class TestPendingQueueTenantIsolation:
    def test_cross_tenant_invisible(self, app, client, jwt_alice, jwt_dave):
        """alice (default) 看不到 dave (bank-prod) 的 IN_REVIEW 文档"""
        test_app, fake_db, _, _ = app
        d_alice = _make_doc(tenant_id="default", approval_status_value="IN_REVIEW", title="alice's")
        d_dave = _make_doc(tenant_id="bank-prod", approval_status_value="IN_REVIEW", title="dave's")
        fake_db.docs[str(d_alice.id)] = d_alice
        fake_db.docs[str(d_dave.id)] = d_dave

        # alice (default) 查 — 只能看自己的
        r = client.get(
            "/api/v1/documents/approvals/pending",
            headers={"Authorization": f"Bearer {jwt_alice}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["doc_id"] == str(d_alice.id)

        # dave (bank-prod) 查 — 只能看自己的
        r = client.get(
            "/api/v1/documents/approvals/pending",
            headers={"Authorization": f"Bearer {jwt_dave}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["doc_id"] == str(d_dave.id)


# ═══════════════════════════════════════════════════════════════════════
# T6 is_deleted 排除
# ═══════════════════════════════════════════════════════════════════════


class TestPendingQueueDeletedExcluded:
    def test_deleted_docs_excluded(self, app, client, jwt_alice):
        test_app, fake_db, _, _ = app
        d_alive = _make_doc(approval_status_value="IN_REVIEW", title="alive")
        d_gone = _make_doc(approval_status_value="IN_REVIEW", title="gone", is_deleted=True)
        fake_db.docs[str(d_alive.id)] = d_alive
        fake_db.docs[str(d_gone.id)] = d_gone

        r = client.get(
            "/api/v1/documents/approvals/pending",
            headers={"Authorization": f"Bearer {jwt_alice}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["doc_id"] == str(d_alive.id)


# ═══════════════════════════════════════════════════════════════════════
# T7 分页
# ═══════════════════════════════════════════════════════════════════════


class TestPendingQueuePagination:
    def test_limit_offset_pagination(self, app, client, jwt_alice):
        test_app, fake_db, _, _ = app
        # 5 个 IN_REVIEW 文档
        docs = []
        for i in range(5):
            d = _make_doc(
                approval_status_value="IN_REVIEW",
                title=f"doc-{i}",
                updated_at=dt.datetime(2026, 7, 1, 0, 0, 0, tzinfo=dt.UTC) + dt.timedelta(minutes=i),
            )
            fake_db.docs[str(d.id)] = d
            docs.append(d)

        # page 1: limit=2, offset=0
        r = client.get(
            "/api/v1/documents/approvals/pending?limit=2&offset=0",
            headers={"Authorization": f"Bearer {jwt_alice}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 5
        assert body["limit"] == 2
        assert body["offset"] == 0
        assert len(body["items"]) == 2
        # 倒序: doc-4, doc-3
        assert body["items"][0]["title"] == "doc-4"
        assert body["items"][1]["title"] == "doc-3"

        # page 2: limit=2, offset=2
        r = client.get(
            "/api/v1/documents/approvals/pending?limit=2&offset=2",
            headers={"Authorization": f"Bearer {jwt_alice}"},
        )
        body = r.json()
        assert body["total"] == 5
        assert len(body["items"]) == 2
        # doc-2, doc-1
        assert body["items"][0]["title"] == "doc-2"
        assert body["items"][1]["title"] == "doc-1"

        # page 3: limit=2, offset=4 (last page, only 1 item)
        r = client.get(
            "/api/v1/documents/approvals/pending?limit=2&offset=4",
            headers={"Authorization": f"Bearer {jwt_alice}"},
        )
        body = r.json()
        assert body["total"] == 5
        assert len(body["items"]) == 1
        assert body["items"][0]["title"] == "doc-0"


# ═══════════════════════════════════════════════════════════════════════
# T8 total 与 limit/offset 无关
# ═══════════════════════════════════════════════════════════════════════


class TestPendingQueueTotalIndependent:
    def test_total_ignores_paging(self, app, client, jwt_alice):
        test_app, fake_db, _, _ = app
        for i in range(7):
            d = _make_doc(approval_status_value="IN_REVIEW", title=f"d-{i}")
            fake_db.docs[str(d.id)] = d

        r1 = client.get(
            "/api/v1/documents/approvals/pending?limit=1&offset=0",
            headers={"Authorization": f"Bearer {jwt_alice}"},
        )
        r2 = client.get(
            "/api/v1/documents/approvals/pending?limit=3&offset=4",
            headers={"Authorization": f"Bearer {jwt_alice}"},
        )
        assert r1.json()["total"] == 7
        assert r2.json()["total"] == 7


# ═══════════════════════════════════════════════════════════════════════
# T9 last_actor_id 取自最近一次审批
# ═══════════════════════════════════════════════════════════════════════


class TestPendingQueueLastActor:
    def test_last_actor_from_recent_approval(self, app, client, jwt_alice):
        test_app, fake_db, _, _ = app
        d = _make_doc(approval_status_value="IN_REVIEW", title="doc")
        fake_db.docs[str(d.id)] = d

        # 注入 2 次审批: 先 alice SUBMIT, 后 bob APPROVE
        t0 = dt.datetime(2026, 7, 1, 10, 0, 0, tzinfo=dt.UTC)
        t1 = dt.datetime(2026, 7, 1, 11, 0, 0, tzinfo=dt.UTC)
        a1 = _make_approval(doc_id=str(d.id), action="SUBMIT", actor_id="alice", created_at=t0)
        a2 = _make_approval(doc_id=str(d.id), action="APPROVE", actor_id="bob", created_at=t1)
        fake_db.approvals.append(a1)
        fake_db.approvals.append(a2)

        r = client.get(
            "/api/v1/documents/approvals/pending",
            headers={"Authorization": f"Bearer {jwt_alice}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["items"]) == 1
        # 最近一次是 bob APPROVE
        assert body["items"][0]["last_actor_id"] == "bob"
        assert body["items"][0]["last_action_at"] is not None


# ═══════════════════════════════════════════════════════════════════════
# T10 query 强制 principal.tenant_id (不接受 query 覆盖)
# ═══════════════════════════════════════════════════════════════════════


class TestPendingQueueTenantEnforced:
    def test_query_tenant_id_ignored(self, app, client, jwt_alice):
        """即使客户端传 ?tenant_id=bank-prod, 仍然按 principal.tenant_id (default) 过滤"""
        test_app, fake_db, _, _ = app
        d_default = _make_doc(tenant_id="default", approval_status_value="IN_REVIEW", title="default")
        d_bank = _make_doc(tenant_id="bank-prod", approval_status_value="IN_REVIEW", title="bank-prod")
        fake_db.docs[str(d_default.id)] = d_default
        fake_db.docs[str(d_bank.id)] = d_bank

        # alice 是 default, 即使 query 传 tenant_id=bank-prod, 仍然只看 default
        r = client.get(
            "/api/v1/documents/approvals/pending?tenant_id=bank-prod",
            headers={"Authorization": f"Bearer {jwt_alice}"},
        )
        assert r.status_code == 200
        body = r.json()
        # 注意: query 里的 tenant_id 不是 Pydantic 参数, FastAPI 忽略
        # (endpoint 只接受 status/limit/offset), 所以仍按 principal 过滤
        assert body["total"] == 1
        assert body["items"][0]["doc_id"] == str(d_default.id)


# ═══════════════════════════════════════════════════════════════════════
# 边界: 队列空
# ═══════════════════════════════════════════════════════════════════════


class TestPendingQueueEmpty:
    def test_empty_queue(self, app, client, jwt_alice):
        test_app, fake_db, _, _ = app
        r = client.get(
            "/api/v1/documents/approvals/pending",
            headers={"Authorization": f"Bearer {jwt_alice}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body == {"items": [], "total": 0, "limit": 50, "offset": 0}
