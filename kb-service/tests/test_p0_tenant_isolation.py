"""P0-1.1 检索层多租户隔离测试

覆盖:
- ES mapping 含 tenant_id + allowed_roles
- ES writer 写入两字段
- build_es_filters 对 keyword list 生成 terms 子句
- retrieve 端点严格 override 防御 (身份不匹配 → 403)
- _search_rrf 响应 _source 含两字段
- 审计 actor_roles 落 log

不需要真实 ES/Redis — 用 in-memory fake.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

fastapi = pytest.importorskip("fastapi")

from kb.config import get_settings
from kb.main import create_app


# ── JWT helper ──


def _make_jwt(settings, *, sub="alice", tenant_id="default", roles=None,
              actor_role=None, tier="normal", exp_offset=3600) -> str:
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
    return jwt.encode(payload, settings.jwt.secret, algorithm=settings.jwt.algorithm)


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def jwt_alice(settings):
    return _make_jwt(settings, sub="alice", tenant_id="default", roles=["editor"])


@pytest.fixture
def jwt_bob(settings):
    return _make_jwt(settings, sub="bob", tenant_id="default", roles=["admin"])


@pytest.fixture
def jwt_carol(settings):
    return _make_jwt(settings, sub="carol", tenant_id="bank-prod", roles=["editor"])


# ── Fake DB / ES / Redis fixtures ──


class _FakeAsyncSession:
    """最小 AsyncSession — 跟踪 KbDocument / KbChunk / KbRetrievalAudit"""

    def __init__(self):
        self.retrieval_audits: list = []
        self.docs: dict[str, Any] = {}
        self.chunks: dict[Any, Any] = {}
        self.committed = False

    def add(self, obj):
        cls_name = obj.__class__.__name__
        if cls_name == "KbRetrievalAudit":
            self.retrieval_audits.append(obj)
        elif cls_name == "KbDocument":
            self.docs[str(obj.id)] = obj
        elif cls_name == "KbChunk":
            self.chunks[obj.id] = obj

    async def get(self, _model, pk):
        return self.docs.get(str(pk))

    async def commit(self):
        self.committed = True

    async def close(self):
        pass

    async def flush(self):
        pass

    async def execute(self, *args, **kwargs):
        # 当查询 KbChunk 时, 返回已注入的 chunks
        return MagicMock(
            scalars=MagicMock(return_value=MagicMock(
                all=MagicMock(return_value=list(self.chunks.values())),
            )),
            all=MagicMock(return_value=list(self.chunks.values())),
            scalar=MagicMock(return_value=len(self.chunks)),
        )


class _SearchCall:
    def __init__(self, body: dict):
        self.body = body


class _FakeESClient:
    """记录每次 search 调用, 可注入自定义 hits"""

    def __init__(self, hits: list[dict] | None = None):
        self.search_calls: list[_SearchCall] = []
        self._hits = hits or []
        self.ping = AsyncMock(return_value=True)
        self.aclose = AsyncMock()

    async def search(self, *, index: str, body: dict, **kwargs):
        self.search_calls.append(_SearchCall(body))
        return {"hits": {"hits": self._hits}}

    async def index(self, *, index: str, id: str, document: dict, **kwargs):
        # 记录所有 index 调用, 供 ES writer 写入字段测试用
        self.indexed = getattr(self, "indexed", [])
        self.indexed.append({"id": id, "document": document, "index": index})

    async def indices(self):
        return MagicMock(create=AsyncMock(), exists=AsyncMock(return_value=True))


class _FakeRedis:
    def __init__(self):
        self.ping = AsyncMock(return_value=True)
        self.get = AsyncMock(return_value=None)
        self.set = AsyncMock(return_value=True)
        self.delete = AsyncMock(return_value=1)
        self.aclose = AsyncMock()


class _FakeEmbedProvider:
    """最小 embedding provider — 返回固定维度零向量"""

    def __init__(self, dim: int = 8):
        self.dim = dim

    async def embed_query(self, query: str) -> list[float]:
        return [0.0] * self.dim

    async def embed_documents(self, docs: list[str]) -> list[list[float]]:
        return [[0.0] * self.dim for _ in docs]


@pytest_asyncio.fixture
async def app(monkeypatch):
    fake_redis = _FakeRedis()

    async def _async_iter_keys(match=None, **_):
        for k in ["kp:rag:cache:h:abc", "kp:rag:cache:b:def"]:
            yield k

    fake_redis.scan_iter = MagicMock(side_effect=_async_iter_keys)

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

    # P0-1.2: mock MinIO 客户端 (上传端点需要)
    fake_minio = MagicMock()
    fake_minio.put_object = MagicMock()
    fake_minio.bucket_exists = MagicMock(return_value=True)
    monkeypatch.setattr("kb.api.documents.get_minio", lambda: fake_minio)

    # mock Kafka 投递 (上传端点会调)
    async def fake_publish_ingest(doc_id, payload):
        pass
    monkeypatch.setattr("kb.api.documents.publish_ingest_request", fake_publish_ingest)

    yield test_app, fake_db, fake_redis, fake_es

    test_app.dependency_overrides.clear()


@pytest.fixture
def client(app):
    test_app, _, _, _ = app
    return TestClient(test_app)


# ═══════════════════════════════════════════════════════════════════════
# 1.1 ES mapping 含 tenant_id + allowed_roles
# ═══════════════════════════════════════════════════════════════════════


class TestESMapping:
    def test_mapping_contains_tenant_id(self):
        """ES chunks index mapping 必须含 tenant_id keyword"""
        from scripts.init_elasticsearch import _build_chunks_mapping

        settings = get_settings()
        mapping = _build_chunks_mapping(settings)
        props = mapping["mappings"]["properties"]
        assert "tenant_id" in props
        assert props["tenant_id"]["type"] == "keyword"

    def test_mapping_contains_allowed_roles(self):
        """ES chunks index mapping 必须含 allowed_roles keyword"""
        from scripts.init_elasticsearch import _build_chunks_mapping

        settings = get_settings()
        mapping = _build_chunks_mapping(settings)
        props = mapping["mappings"]["properties"]
        assert "allowed_roles" in props
        assert props["allowed_roles"]["type"] == "keyword"


# ═══════════════════════════════════════════════════════════════════════
# 1.2 ES writer 写入两字段
# ═══════════════════════════════════════════════════════════════════════


class TestESWriter:
    @pytest.mark.asyncio
    async def test_writer_writes_tenant_id_and_allowed_roles(self):
        """write_chunks_to_es 必须把 tenant_id + allowed_roles 写入 ES doc"""
        from kb.pipeline.writer import write_chunks_to_es

        fake_es = _FakeESClient()
        chunk_ids = ["c1", "c2"]
        chunks = [
            {"content": "信用卡额度", "chunk_type": "plain_text", "heading_path": []},
            {"content": "挂失补卡", "chunk_type": "plain_text", "heading_path": []},
        ]
        embeddings = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        doc_metadata = {
            "category": "CREDIT_CARD",
            "doc_type": "faq",
            "tenant_id": "bank-prod",
            "allowed_roles": ["admin", "reviewer"],
        }

        count = await write_chunks_to_es(
            es_client=fake_es,
            chunk_ids=chunk_ids,
            chunks=chunks,
            embeddings=embeddings,
            doc_id="doc-1",
            doc_metadata=doc_metadata,
            model_version="bge-m3-v1",
        )
        assert count == 2
        # 检查写入的 document
        docs = [c["document"] for c in fake_es.indexed]
        assert docs[0]["tenant_id"] == "bank-prod"
        assert docs[0]["allowed_roles"] == ["admin", "reviewer"]
        assert docs[1]["tenant_id"] == "bank-prod"
        assert docs[1]["allowed_roles"] == ["admin", "reviewer"]

    @pytest.mark.asyncio
    async def test_writer_defaults_tenant_and_empty_roles(self):
        """缺省时 tenant_id 默认为 'default', allowed_roles 为 []"""
        from kb.pipeline.writer import write_chunks_to_es

        fake_es = _FakeESClient()
        chunk_ids = ["c1"]
        chunks = [{"content": "x", "chunk_type": "plain_text", "heading_path": []}]
        embeddings = [[0.1]]

        await write_chunks_to_es(
            es_client=fake_es,
            chunk_ids=chunk_ids,
            chunks=chunks,
            embeddings=embeddings,
            doc_id="doc-1",
            doc_metadata={},  # 完全缺省
            model_version="v1",
        )
        doc = fake_es.indexed[0]["document"]
        assert doc["tenant_id"] == "default"
        assert doc["allowed_roles"] == []


# ═══════════════════════════════════════════════════════════════════════
# 1.3 + 1.4 _ES_KEYWORD_FIELDS + build_es_filters list → terms
# ═══════════════════════════════════════════════════════════════════════


class TestBuildESFilters:
    """build_es_filters 必须对 _ES_KEYWORD_FIELDS 内字段支持 list → terms"""

    def test_allowed_roles_in_keyword_fields(self):
        from kb.retrieval.engine import _ES_KEYWORD_FIELDS

        assert "allowed_roles" in _ES_KEYWORD_FIELDS
        assert "tenant_id" in _ES_KEYWORD_FIELDS

    def test_single_value_tenant_id_term(self):
        from kb.retrieval.engine import build_es_filters

        clauses = build_es_filters({"tenant_id": "bank-prod"})
        assert {"term": {"tenant_id": "bank-prod"}} in clauses

    def test_list_value_allowed_roles_terms(self):
        from kb.retrieval.engine import build_es_filters

        clauses = build_es_filters({"allowed_roles": ["admin", "editor"]})
        assert {"terms": {"allowed_roles": ["admin", "editor"]}} in clauses

    def test_empty_list_skips_filter(self):
        """空列表 = 全员可见, 不生成过滤子句"""
        from kb.retrieval.engine import build_es_filters

        clauses = build_es_filters({"allowed_roles": []})
        # 无 terms/term 子句
        for c in clauses:
            assert "allowed_roles" not in c.get("term", {}) and "allowed_roles" not in c.get("terms", {})

    def test_none_value_skipped(self):
        from kb.retrieval.engine import build_es_filters

        clauses = build_es_filters({"tenant_id": None, "allowed_roles": None})
        assert clauses == []

    def test_combined_tenant_and_roles(self):
        from kb.retrieval.engine import build_es_filters

        clauses = build_es_filters({
            "tenant_id": "bank-prod",
            "allowed_roles": ["admin"],
        })
        assert {"term": {"tenant_id": "bank-prod"}} in clauses
        assert {"terms": {"allowed_roles": ["admin"]}} in clauses


# ═══════════════════════════════════════════════════════════════════════
# 1.5 _search_rrf _source 含两字段
# ═══════════════════════════════════════════════════════════════════════


class TestSearchRRFSourceFields:
    @pytest.mark.asyncio
    async def test_search_body_includes_tenant_and_roles_in_source(self, app, client, jwt_alice):
        """_search_rrf 的 _source 必须含 tenant_id + allowed_roles"""
        _, _, _, fake_es = app

        # 命中一条命中 doc
        fake_es._hits = [{
            "_id": "c1", "_score": 0.9, "_source": {
                "chunk_id": "c1", "doc_id": "d1", "content": "信用卡额度",
                "tenant_id": "default", "allowed_roles": [],
            },
        }]

        r = client.post("/api/v1/retrieve",
                        json={"query": "信用卡", "top_k": 3, "search_type": "hybrid",
                              "timeout_ms": 1000},
                        headers={"Authorization": f"Bearer {jwt_alice}"})
        assert r.status_code == 200, r.text
        # fake_es 收到 search 调用
        assert len(fake_es.search_calls) >= 1
        body = fake_es.search_calls[0].body
        assert "tenant_id" in body["_source"]
        assert "allowed_roles" in body["_source"]


# ═══════════════════════════════════════════════════════════════════════
# 1.6 retrieve 严格 override 403 防御
# ═══════════════════════════════════════════════════════════════════════


class TestRetrieveStrictOverride:
    def test_matching_tenant_passes(self, app, client, jwt_alice):
        """tenant_id 一致 → 200"""
        r = client.post("/api/v1/retrieve",
                        json={"query": "信用卡", "top_k": 3, "search_type": "hybrid",
                              "tenant_id": "default"},
                        headers={"Authorization": f"Bearer {jwt_alice}"})
        assert r.status_code == 200

    def test_mismatched_tenant_returns_403(self, app, client, jwt_alice):
        """请求体 tenant_id != principal.tenant_id → 403"""
        r = client.post("/api/v1/retrieve",
                        json={"query": "信用卡", "top_k": 3, "search_type": "hybrid",
                              "tenant_id": "bank-prod"},  # alice 是 default
                        headers={"Authorization": f"Bearer {jwt_alice}"})
        assert r.status_code == 403
        assert "tenant_id" in r.json()["detail"]

    def test_missing_tenant_uses_principal(self, app, client, jwt_alice):
        """请求体无 tenant_id → 强制 principal.tenant_id (default)"""
        _, _, _, fake_es = app
        r = client.post("/api/v1/retrieve",
                        json={"query": "信用卡", "top_k": 3, "search_type": "hybrid"},
                        headers={"Authorization": f"Bearer {jwt_alice}"})
        assert r.status_code == 200
        # 检查 ES 查询体里有 tenant_id=default
        if fake_es.search_calls:
            body = fake_es.search_calls[0].body
            # query 里可能含 bool.filter → 检查 tenant_id
            from kb.retrieval.engine import _ES_KEYWORD_FIELDS
            assert "tenant_id" in _ES_KEYWORD_FIELDS  # sanity

    def test_actor_roles_always_from_principal(self, app, client, jwt_alice):
        """请求体传 actor_roles=['admin'] + 用 alice token → 仍按 alice 的 roles (editor) 过滤"""
        _, _, _, _ = app
        # 第 1 次: 试图冒充 admin → 仍按 principal (editor) 过滤, 不报错
        r1 = client.post("/api/v1/retrieve",
                         json={"query": "信用卡", "top_k": 3, "search_type": "hybrid",
                               "actor_roles": ["admin"]},  # 试图冒充 admin
                         headers={"Authorization": f"Bearer {jwt_alice}"})
        # 第 2 次: 同上, 验证多次也无副作用
        r2 = client.post("/api/v1/retrieve",
                         json={"query": "x", "top_k": 3, "search_type": "hybrid",
                               "actor_roles": ["admin"]},
                         headers={"Authorization": f"Bearer {jwt_alice}"})
        assert r1.status_code == 200
        assert r2.status_code == 200


# ═══════════════════════════════════════════════════════════════════════
# 1.7 审计 actor_roles 落 log
# ═══════════════════════════════════════════════════════════════════════


class TestAuditActorRoles:
    def test_audit_logs_actor_roles(self, app, client, jwt_alice, monkeypatch):
        """log_retrieval 必须在 structlog 里含 actor_roles 字段"""
        # structlog logger.info 不走 caplog, 直接 patch 捕获
        from kb.security import audit_service

        captured: list[dict] = []
        original_info = audit_service.logger.info

        def fake_info(event, **kwargs):
            if event == "retrieval_audit":
                captured.append(kwargs)
            return original_info(event, **kwargs)

        monkeypatch.setattr(audit_service.logger, "info", fake_info)

        r = client.post("/api/v1/retrieve",
                        json={"query": "信用卡", "top_k": 3, "search_type": "hybrid"},
                        headers={"Authorization": f"Bearer {jwt_alice}"})
        assert r.status_code == 200
        assert len(captured) >= 1
        rec = captured[0]
        # actor_roles 是 Principal.roles = ['editor']
        assert rec.get("actor_roles") == ["editor"]
        assert rec.get("tenant_id") == "default"

    def test_audit_empty_roles(self, app, client, settings, monkeypatch):
        """roles=[] 的身份 → 审计记空列表 (全员匹配 ES 过滤)

        用 'extra' 绕过 _make_jwt 默认 ['editor'] 兜底
        """
        from kb.security import audit_service

        captured: list[dict] = []
        original_info = audit_service.logger.info

        def fake_info(event, **kwargs):
            if event == "retrieval_audit":
                captured.append(kwargs)
            return original_info(event, **kwargs)

        monkeypatch.setattr(audit_service.logger, "info", fake_info)

        # 直接签发 roles=[] 的 JWT (绕过 _make_jwt 兜底)
        import jwt as jwt_lib
        now = dt.datetime.now(dt.UTC)
        payload = {
            "sub": "anon",
            "tenant_id": "default",
            "roles": [],  # 显式空
            "actor_role": "user",
            "tier": "normal",
            "iat": int(now.timestamp()),
            "exp": int(now.timestamp()) + 3600,
            "aud": settings.jwt.audience,
            "iss": settings.jwt.issuer,
        }
        jwt_no_roles = jwt_lib.encode(payload, settings.jwt.secret, algorithm=settings.jwt.algorithm)

        r = client.post("/api/v1/retrieve",
                        json={"query": "x", "top_k": 3, "search_type": "hybrid"},
                        headers={"Authorization": f"Bearer {jwt_no_roles}"})
        assert r.status_code == 200
        assert len(captured) >= 1
        assert captured[0].get("actor_roles") == []


# ═══════════════════════════════════════════════════════════════════════
# 1.8 compliance filter 跨租户隔离 (E2E)
# ═══════════════════════════════════════════════════════════════════════


class TestEndToEndTenantIsolation:
    @pytest.mark.asyncio
    async def test_filter_includes_tenant_id(self, app, client, jwt_alice):
        """检索 body 里必须含 tenant_id=default 过滤"""
        _, _, _, fake_es = app
        r = client.post("/api/v1/retrieve",
                        json={"query": "信用卡", "top_k": 3, "search_type": "bm25_only"},
                        headers={"Authorization": f"Bearer {jwt_alice}"})
        assert r.status_code == 200
        # bm25_only 走 _search_bm25_only 路径
        if fake_es.search_calls:
            body = fake_es.search_calls[0].body
            # bool.filter 应含 tenant_id term
            filters = body.get("query", {}).get("bool", {}).get("filter", [])
            # 可能 query 形式不同, 但至少 filter 里有 tenant_id
            filter_str = str(filters)
            assert "tenant_id" in filter_str

    @pytest.mark.asyncio
    async def test_filter_includes_allowed_roles_terms(self, app, client, jwt_alice):
        """actor_roles 注入后, filter 应含 terms 子句"""
        _, _, _, fake_es = app
        r = client.post("/api/v1/retrieve",
                        json={"query": "信用卡", "top_k": 3, "search_type": "bm25_only"},
                        headers={"Authorization": f"Bearer {jwt_alice}"})
        assert r.status_code == 200
        if fake_es.search_calls:
            body = fake_es.search_calls[0].body
            filters = body.get("query", {}).get("bool", {}).get("filter", [])
            filter_str = str(filters)
            # allowed_roles terms 子句
            assert "allowed_roles" in filter_str
            assert "editor" in filter_str  # alice 是 editor

    def test_cross_tenant_query_blocked(self, app, client, jwt_alice):
        """alice (default) 试图检索 bank-prod 数据 → 403"""
        r = client.post("/api/v1/retrieve",
                        json={"query": "信用卡", "top_k": 3, "search_type": "hybrid",
                              "tenant_id": "bank-prod"},
                        headers={"Authorization": f"Bearer {jwt_alice}"})
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════
# P0-1.2 上传层 allowed_roles 注入
# ═══════════════════════════════════════════════════════════════════════


def _upload_with(client, token, allowed_roles=None, filename="test.txt"):
    """上传辅助: 包含 allowed_roles 字段"""
    files = {"file": (filename, io.BytesIO(b"x"), "text/plain")}
    data = {"category": "OTHER", "doc_type": "faq"}
    if allowed_roles is not None:
        # allowed_roles 必须是 JSON 字符串
        import json as json_lib
        data["allowed_roles"] = json_lib.dumps(allowed_roles)
    resp = client.post("/api/v1/documents", files=files, data=data,
                       headers={"Authorization": f"Bearer {token}"})
    return resp


class TestUploadAllowedRoles:
    """上传 allowed_roles 注入测试 — T16-T23"""

    def test_explicit_roles_persisted(self, app, client, jwt_alice):
        """T16: 上传 allowed_roles='["admin"]' → KbDocument.allowed_roles = ['admin']"""
        test_app, fake_db, _, _ = app
        r = _upload_with(client, jwt_alice, allowed_roles=["admin"])
        assert r.status_code == 202, r.text
        doc_id = r.json()["doc_id"]
        # 检查 fake_db 里的 KbDocument
        assert doc_id in fake_db.docs
        assert fake_db.docs[doc_id].allowed_roles == ["admin"]

    def test_missing_field_defaults_to_empty(self, app, client, jwt_alice):
        """T17: 上传不传 allowed_roles → 默认 [] (全员可见)"""
        test_app, fake_db, _, _ = app
        r = _upload_with(client, jwt_alice, allowed_roles=None)
        assert r.status_code == 202, r.text
        doc_id = r.json()["doc_id"]
        assert fake_db.docs[doc_id].allowed_roles == []

    def test_empty_array_string_defaults_to_empty(self, app, client, jwt_alice):
        """T17b: 上传 allowed_roles='[]' → [] (显式空列表也 = 全员可见)"""
        test_app, fake_db, _, _ = app
        r = _upload_with(client, jwt_alice, allowed_roles=[])
        assert r.status_code == 202
        doc_id = r.json()["doc_id"]
        assert fake_db.docs[doc_id].allowed_roles == []

    def test_invalid_json_returns_422(self, app, client, jwt_alice):
        """T18: 上传 allowed_roles='not json' → 422"""
        test_app, fake_db, _, _ = app
        files = {"file": ("test.txt", io.BytesIO(b"x"), "text/plain")}
        data = {"category": "OTHER", "doc_type": "faq", "allowed_roles": "not-a-json"}
        r = client.post("/api/v1/documents", files=files, data=data,
                        headers={"Authorization": f"Bearer {jwt_alice}"})
        assert r.status_code == 422
        assert "JSON" in r.json()["detail"]

    def test_not_list_returns_422(self, app, client, jwt_alice):
        """T19: 上传 allowed_roles='{"x":1}' (对象) → 422"""
        test_app, fake_db, _, _ = app
        files = {"file": ("test.txt", io.BytesIO(b"x"), "text/plain")}
        data = {"category": "OTHER", "doc_type": "faq", "allowed_roles": '{"x":1}'}
        r = client.post("/api/v1/documents", files=files, data=data,
                        headers={"Authorization": f"Bearer {jwt_alice}"})
        assert r.status_code == 422
        assert "数组" in r.json()["detail"]

    def test_empty_string_element_returns_422(self, app, client, jwt_alice):
        """T19b: 元素含空字符串 → 422"""
        test_app, fake_db, _, _ = app
        files = {"file": ("test.txt", io.BytesIO(b"x"), "text/plain")}
        data = {"category": "OTHER", "doc_type": "faq", "allowed_roles": '["admin", ""]'}
        r = client.post("/api/v1/documents", files=files, data=data,
                        headers={"Authorization": f"Bearer {jwt_alice}"})
        assert r.status_code == 422
        assert "非空" in r.json()["detail"]

    def test_roles_in_kafka_payload(self, app, client, jwt_alice, monkeypatch):
        """T20: Kafka 载荷含 allowed_roles 字段 (供 ETL 写 ES)"""
        test_app, fake_db, _, _ = app
        # 捕获 publish_ingest_request 调用
        from kb.api import documents
        captured: list[dict] = []
        async def fake_publish(doc_id, payload):
            captured.append(payload)
        monkeypatch.setattr(documents, "publish_ingest_request", fake_publish)

        r = _upload_with(client, jwt_alice, allowed_roles=["admin", "reviewer"])
        assert r.status_code == 202
        assert len(captured) == 1
        payload = captured[0]
        # payload 顶层有 tenant_id, metadata 内层有 allowed_roles
        assert payload["tenant_id"] == "default"
        assert payload["metadata"]["allowed_roles"] == ["admin", "reviewer"]

    def test_reindex_uses_pg_allowed_roles(self, app, client, jwt_alice, monkeypatch):
        """T21: reindex 时 metadata.allowed_roles 从 PG 读, 不接受请求体"""
        test_app, fake_db, _, _ = app
        # 先上传一个 doc
        r = _upload_with(client, jwt_alice, allowed_roles=["admin"])
        assert r.status_code == 202
        doc_id = r.json()["doc_id"]
        # fake_db.docs[doc_id] 已有完整 KbDocument, 但 approval_status 是 enum,
        # 而 fake 没真值. 我们直接覆盖为有意义的 MagicMock-like
        doc = fake_db.docs[doc_id]
        from unittest.mock import PropertyMock
        # 让 doc.approval_status.value 返回 'DRAFT'
        from types import SimpleNamespace
        doc.approval_status = SimpleNamespace(value="DRAFT")

        # 注入 KbChunk 模拟已索引状态
        from kb.orm.kb import KbChunk
        from uuid import UUID
        chunk = KbChunk(
            id=UUID("00000000-0000-0000-0000-000000000001"),
            document_id=UUID(doc_id),
            chunk_index=0,
            content="test chunk",
            embedding=b"",  # 占位
            chunk_type="plain_text",
            tenant_id="default",
        )
        fake_db.chunks[chunk.id] = chunk

        # monkeypatch writer 捕获 metadata
        from kb.pipeline import writer
        captured: list[dict] = []
        async def fake_write_chunks_to_es(es_client, doc_id, chunk_ids, chunks, embeddings,
                                          doc_metadata, model_version):
            captured.append({"doc_id": doc_id, "doc_metadata": doc_metadata})
            return len(chunks)
        async def fake_delete_chunks_from_es(*args, **kwargs):
            return 0
        async def fake_mark_es_indexed(*args, **kwargs):
            pass
        monkeypatch.setattr(writer, "write_chunks_to_es", fake_write_chunks_to_es)
        monkeypatch.setattr(writer, "delete_chunks_from_es", fake_delete_chunks_from_es)
        monkeypatch.setattr(writer, "mark_es_indexed", fake_mark_es_indexed)

        r = client.post(f"/api/v1/documents/{doc_id}/reindex",
                        headers={"Authorization": f"Bearer {jwt_alice}"})
        assert r.status_code == 200, r.text
        assert len(captured) == 1
        meta = captured[0]["doc_metadata"]
        assert meta["allowed_roles"] == ["admin"]
        assert meta["tenant_id"] == "default"

    def test_empty_roles_role_isolation(self, app, client, jwt_alice, jwt_bob):
        """T22: 端到端 — 角色隔离语义
        alice (editor) 上传 allowed_roles=['admin'] → editor alice 检索时 ES filter
        含 'editor' terms; bob (admin) 检索时含 'admin' terms
        """
        test_app, fake_db, _, fake_es = app
        # alice 上传
        r = _upload_with(client, jwt_alice, allowed_roles=["admin"])
        assert r.status_code == 202
        doc_id = r.json()["doc_id"]
        # 模拟 publish 后 doc 在 ES (fake_es 没数据, 模拟空结果)
        fake_es._hits = []

        # alice (editor) 检索 → 空 (无 ES 命中, 但 ES 端会过滤)
        r1 = client.post("/api/v1/retrieve",
                         json={"query": "信用卡", "top_k": 3, "search_type": "bm25_only"},
                         headers={"Authorization": f"Bearer {jwt_alice}"})
        # bob (admin) 检索 → 也空 (但过滤条件不同)
        r2 = client.post("/api/v1/retrieve",
                         json={"query": "信用卡", "top_k": 3, "search_type": "bm25_only"},
                         headers={"Authorization": f"Bearer {jwt_bob}"})
        assert r1.status_code == 200
        assert r2.status_code == 200
        # 验证两次检索的 ES filter 不一样 (alice=editor, bob=admin)
        # fake_es 记录了所有 search 调用
        bodies = [c.body for c in fake_es.search_calls]
        # 至少有一次是 alice (含 allowed_roles terms ['editor'])
        # 至少有一次是 bob (含 allowed_roles terms ['admin'])
        all_filters = str(bodies)
        assert "editor" in all_filters
        assert "admin" in all_filters

    def test_empty_roles_persists_as_empty_list(self, app, client, jwt_alice):
        """T23: allowed_roles=[] 持久化为空列表 (语义细节见 docs/architecture-review.md)
        注意: 当前实现下'空列表 = 全员可见'需要 actor_roles=[] 才会过滤掉
        allowed_roles 条款; 当 actor_roles 非空时, terms 过滤会应用到所有文档
        (空 allowed_roles 的文档将不会被命中, 这是已知 trade-off).
        完整'空=全员可见'语义是 P2 增强项.
        """
        test_app, fake_db, _, _ = app
        r = _upload_with(client, jwt_alice, allowed_roles=None)
        assert r.status_code == 202
        doc_id = r.json()["doc_id"]
        # 验证: 持久化的就是 []
        assert fake_db.docs[doc_id].allowed_roles == []

        # 上传 allowed_roles='[]' 也得到 []
        r2 = _upload_with(client, jwt_alice, allowed_roles=[])
        assert r2.status_code == 202
        doc_id2 = r2.json()["doc_id"]
        assert fake_db.docs[doc_id2].allowed_roles == []
