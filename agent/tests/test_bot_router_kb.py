"""bot/router.py 知识库端点 ASGI 单元测试 (mock db/检索)"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lumio.services.bot import router as bot_router_module
from lumio.services.bot.router import router as bot_router
from lumio.shared.auth import AuthUser, get_current_user
from lumio.shared.middleware import register_exception_handlers


class _FakeDoc:
    def __init__(self, doc_id="d1", title="年费说明", source_type="PDF", category="fee", status="PUBLISHED"):
        self.id = doc_id
        self.title = title
        self.source_type = source_type
        self.category = category
        self.doc_type = "rule"
        self.status = status
        self.chunk_count = 3
        self.created_at = None
        self.is_deleted = False
        self.deleted_at = None


class _FakeResult:
    def __init__(self, one=None, all_rows=None, scalar_val=None):
        self._one = one
        self._rows = all_rows or []
        self._scalar = scalar_val

    def scalar_one_or_none(self):
        return self._one

    def scalars(self):
        return _Scalars(self._rows)

    def scalar(self):
        return self._scalar


class _Scalars:
    """scalars() 返回对象"""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDb:
    """mock db session (DbSession 依赖)"""

    def __init__(self, one=None, all_rows=None, scalar_val=0):
        self.result = _FakeResult(one=one, all_rows=all_rows, scalar_val=scalar_val)
        self.committed = False
        self.flushed = False

    async def execute(self, stmt, *a, **kw):
        return self.result

    async def commit(self):
        self.committed = True

    async def flush(self):
        self.flushed = True

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = "doc-upload-1"
        self.added = obj


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(bot_router, prefix="/api")
    register_exception_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: AuthUser(user_id="u1", role="admin", session_id=None)
    return app


@pytest.fixture
def setup_state(app: FastAPI) -> _FakeDb:
    from lumio.services.common.deps import get_db_session

    db = _FakeDb(one=_FakeDoc(), scalar_val=1)

    async def _override_db():
        yield db

    app.dependency_overrides[get_db_session] = _override_db
    return db


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── kb/retrieve ──


async def test_kb_retrieve_success(app: FastAPI, monkeypatch):
    """检索成功 (mock retrieve)"""
    import lumio.services.bot.router as br

    # 端点 response_model=RetrieveResponse, fake 需符合模型结构
    fake_result = {
        "results": [{"chunk_id": "c1", "content": "x", "score": 0.9, "source_doc": "d1", "metadata": {}}],
        "total_candidates": 1,
        "latency_ms": 5,
    }

    async def fake_retrieve(**kwargs):
        return fake_result

    monkeypatch.setattr(br, "retrieve", fake_retrieve)

    breaker = MagicMock()
    breaker.is_available = True
    breaker.allow_request = MagicMock(return_value=True)
    breaker.record_success = MagicMock()

    from lumio.services.common.deps import (
        get_embedding_breaker,
        get_es_breaker,
        get_es_client,
        get_milvus_breaker,
        get_milvus_collection,
        get_reranker_provider,
    )

    app.dependency_overrides[get_embedding_breaker] = lambda: breaker
    app.dependency_overrides[get_es_client] = lambda: MagicMock()
    app.dependency_overrides[get_milvus_collection] = lambda: MagicMock()
    app.dependency_overrides[get_reranker_provider] = lambda: MagicMock()
    app.dependency_overrides[get_es_breaker] = lambda: breaker
    app.dependency_overrides[get_milvus_breaker] = lambda: breaker

    async with await _client(app) as c:
        resp = await c.post("/api/kb/retrieve", json={"query": "年费", "top_k": 3})
    assert resp.status_code == 200
    # retrieve 返回 RetrieveResponse (results 列表)
    assert resp.json()["results"][0]["chunk_id"] == "c1"


async def test_kb_retrieve_degraded(app: FastAPI, monkeypatch):
    """嵌入熔断打开 → 降级 BM25 only"""
    import lumio.services.bot.router as br

    captured = {}

    async def fake_retrieve(**kwargs):
        captured.update(kwargs)
        return {"chunks": []}

    monkeypatch.setattr(br, "retrieve", fake_retrieve)

    open_breaker = MagicMock()
    open_breaker.is_available = False  # 嵌入不可用
    closed_breaker = MagicMock()
    closed_breaker.allow_request = MagicMock(return_value=True)
    closed_breaker.record_success = MagicMock()

    from lumio.services.common.deps import (
        get_embedding_breaker,
        get_es_breaker,
        get_es_client,
        get_milvus_breaker,
        get_milvus_collection,
        get_reranker_provider,
    )

    app.dependency_overrides[get_embedding_breaker] = lambda: open_breaker
    app.dependency_overrides[get_es_client] = lambda: MagicMock()
    app.dependency_overrides[get_milvus_collection] = lambda: MagicMock()
    app.dependency_overrides[get_reranker_provider] = lambda: MagicMock()
    app.dependency_overrides[get_es_breaker] = lambda: closed_breaker
    app.dependency_overrides[get_milvus_breaker] = lambda: closed_breaker

    async with await _client(app) as c:
        resp = await c.post("/api/kb/retrieve", json={"query": "年费"})
    assert resp.status_code == 200
    assert captured["embedding_provider"] is None  # 嵌入降级


# ── kb/documents ──


async def test_list_documents(app: FastAPI, setup_state) -> None:
    """文档列表"""
    setup_state.result = _FakeResult(all_rows=[_FakeDoc(), _FakeDoc(doc_id="d2", title="积分规则")], scalar_val=2)
    async with await _client(app) as c:
        resp = await c.get("/api/kb/documents")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["documents"]) == 2


async def test_get_document_status(app: FastAPI, setup_state) -> None:
    """文档状态"""
    async with await _client(app) as c:
        resp = await c.get("/api/kb/documents/d1/status")
    assert resp.status_code == 200
    assert resp.json()["doc_id"] == "d1"


async def test_get_document_status_missing(app: FastAPI, setup_state) -> None:
    """文档不存在 → 400"""
    setup_state.result = _FakeResult(one=None)
    async with await _client(app) as c:
        resp = await c.get("/api/kb/documents/nope/status")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == 2001


async def test_delete_document(app: FastAPI, setup_state) -> None:
    """软删除 + 清理 ES/Milvus"""
    from lumio.services.common.deps import get_es_client, get_milvus_collection

    es = AsyncMock()
    milvus = MagicMock()
    app.dependency_overrides[get_es_client] = lambda: es
    app.dependency_overrides[get_milvus_collection] = lambda: milvus

    async with await _client(app) as c:
        resp = await c.delete("/api/kb/documents/d1")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert setup_state.result._one.is_deleted is True
    es.delete_by_query.assert_awaited_once()
    milvus.delete.assert_called_once()


async def test_delete_document_missing(app: FastAPI, setup_state) -> None:
    """删除不存在 → 400"""
    setup_state.result = _FakeResult(one=None)
    async with await _client(app) as c:
        resp = await c.delete("/api/kb/documents/nope")
    assert resp.status_code == 400


async def test_upload_document_rejects_bad_extension(app: FastAPI, setup_state) -> None:
    """非法扩展名 → 400"""
    app.state.embedding_breaker = MagicMock()
    app.state.embedding_breaker.is_available = True
    app.state.embedding_provider = MagicMock()
    app.state.es_client = MagicMock()
    app.state.milvus_collection = MagicMock()
    app.state.minio_client = MagicMock()
    async with await _client(app) as c:
        resp = await c.post(
            "/api/kb/documents",
            data={"category": "fee", "doc_type": "rule"},
            files={"file": ("bad.exe", b"content", "application/octet-stream")},
        )
    assert resp.status_code == 400
    assert "不支持" in resp.json()["error"]["message"]


# ── upload_document 全链路 (直接调用) ────────────────────────────


class _FakeUpload:
    """模拟 UploadFile"""

    def __init__(self, filename="a.txt", content=b"hello", size=None, content_type="text/plain"):
        self.filename = filename
        self._content = content
        self.size = size
        self.content_type = content_type

    async def read(self) -> bytes:
        return self._content


def _upload_deps(*, db=None, minio=None, breaker=None):
    from lumio.shared.auth import AuthUser

    db = db or _FakeDb()
    return {
        "db": db,
        "es_client": AsyncMock(),
        "milvus_collection": None,
        "minio_client": minio if minio is not None else MagicMock(),
        "embedding_breaker": breaker or MagicMock(is_available=False, provider=None),
        "user": AuthUser(user_id="u1", role="admin", session_id=None),
    }


async def test_upload_document_success_txt(monkeypatch):
    """TXT 上传成功: MinIO 上传 + 摄入 COMPLETED"""
    import lumio.services.common.ingestion as ingestion

    deps = _upload_deps()
    file = _FakeUpload("年费说明.txt", "文本内容".encode())
    with patch.object(ingestion, "ingest_document", new=AsyncMock(return_value="COMPLETED")) as mock_ingest:
        result = await bot_router_module.upload_document(
            deps["db"],
            deps["es_client"],
            deps["milvus_collection"],
            deps["minio_client"],
            deps["embedding_breaker"],
            deps["user"],
            file=file,
            category="fee",
            doc_type="rule",
            security_level="internal",
            version="1.0",
            keywords="",
            card_type=None,
            customer_tier=None,
            effective_date=None,
            expiry_date=None,
        )
    assert result["status"] == "COMPLETED"
    assert result["doc_id"]
    deps["minio_client"].put_object.assert_called_once()
    # TXT 内容直接作为文本摄入
    args = mock_ingest.await_args
    assert args.kwargs["file_path"] == "文本内容"


async def test_upload_document_pdf_uses_object_key(monkeypatch):
    """PDF → 摄入用 MinIO object key 而非内容"""
    import lumio.services.common.ingestion as ingestion

    deps = _upload_deps()
    file = _FakeUpload("年费说明.pdf", b"%PDF-1.4 fake", content_type="application/pdf")
    with patch.object(ingestion, "ingest_document", new=AsyncMock(return_value="COMPLETED")) as mock_ingest:
        result = await bot_router_module.upload_document(
            deps["db"],
            deps["es_client"],
            deps["milvus_collection"],
            deps["minio_client"],
            deps["embedding_breaker"],
            deps["user"],
            file=file,
            category="fee",
            doc_type="rule",
            security_level="internal",
            version="1.0",
            keywords="",
            card_type=None,
            customer_tier=None,
            effective_date=None,
            expiry_date=None,
        )
    assert result["status"] == "COMPLETED"
    args = mock_ingest.await_args
    assert args.kwargs["file_path"] == "fee/年费说明.pdf"
    assert args.kwargs["source_type"].value == "PDF"


async def test_upload_document_ingest_failed(monkeypatch):
    """摄入返回 FAILED → 文档标记失败"""
    import lumio.services.common.ingestion as ingestion

    deps = _upload_deps()
    file = _FakeUpload("a.md", "# 说明".encode())
    with patch.object(ingestion, "ingest_document", new=AsyncMock(return_value="FAILED")):
        result = await bot_router_module.upload_document(
            deps["db"],
            deps["es_client"],
            deps["milvus_collection"],
            deps["minio_client"],
            deps["embedding_breaker"],
            deps["user"],
            file=file,
            category="kb",
            doc_type="manual",
            security_level="internal",
            version="1.0",
            keywords="",
            card_type=None,
            customer_tier=None,
            effective_date=None,
            expiry_date=None,
        )
    assert result["status"] == "FAILED"


async def test_upload_document_ingest_raises(monkeypatch):
    """摄入异常 → 文档标记失败 (不抛错)"""
    import lumio.services.common.ingestion as ingestion

    deps = _upload_deps()
    file = _FakeUpload("a.html", b"<p>hi</p>")
    with patch.object(ingestion, "ingest_document", new=AsyncMock(side_effect=RuntimeError("es down"))):
        result = await bot_router_module.upload_document(
            deps["db"],
            deps["es_client"],
            deps["milvus_collection"],
            deps["minio_client"],
            deps["embedding_breaker"],
            deps["user"],
            file=file,
            category="kb",
            doc_type="manual",
            security_level="internal",
            version="1.0",
            keywords="",
            card_type=None,
            customer_tier=None,
            effective_date=None,
            expiry_date=None,
        )
    assert result["status"] == "FAILED"


async def test_upload_document_metadata_and_dates(monkeypatch):
    """关键词拆分 + 生效/到期日期解析 + 敏感级别"""
    import lumio.services.common.ingestion as ingestion

    deps = _upload_deps()
    file = _FakeUpload("a.txt", "内容".encode())
    with patch.object(ingestion, "ingest_document", new=AsyncMock(return_value="COMPLETED")) as mock_ingest:
        result = await bot_router_module.upload_document(
            deps["db"],
            deps["es_client"],
            deps["milvus_collection"],
            deps["minio_client"],
            deps["embedding_breaker"],
            deps["user"],
            file=file,
            category="promo",
            doc_type="activity",
            card_type="gold",
            customer_tier="vip",
            security_level="confidential",
            version="1.0",
            effective_date="2026-01-01",
            expiry_date="2026-12-31",
            keywords="积分, 换礼, 优惠",
        )
    assert result["status"] == "COMPLETED"
    meta = mock_ingest.await_args.kwargs["metadata"]
    assert meta.keywords == ["积分", "换礼", "优惠"]
    assert meta.card_type == "gold"
    assert str(meta.effective_date) == "2026-01-01"


async def test_upload_document_too_large_by_size_header():
    """Content-Length 超 50MB → 拒绝"""
    deps = _upload_deps()
    file = _FakeUpload("a.txt", b"x", size=60 * 1024 * 1024)
    with pytest.raises(Exception) as exc_info:
        await bot_router_module.upload_document(
            deps["db"],
            deps["es_client"],
            deps["milvus_collection"],
            deps["minio_client"],
            deps["embedding_breaker"],
            deps["user"],
            file=file,
            category="kb",
            doc_type="rule",
            security_level="internal",
            version="1.0",
            keywords="",
            card_type=None,
            customer_tier=None,
            effective_date=None,
            expiry_date=None,
        )
    assert "文件过大" in str(exc_info.value)


async def test_upload_document_no_minio():
    """MinIO 未配置 → 跳过上传仍可入库"""
    import lumio.services.common.ingestion as ingestion

    deps = _upload_deps(minio=None)
    file = _FakeUpload("a.txt", "内容".encode())
    with patch.object(ingestion, "ingest_document", new=AsyncMock(return_value="COMPLETED")):
        result = await bot_router_module.upload_document(
            deps["db"],
            deps["es_client"],
            deps["milvus_collection"],
            None,
            deps["embedding_breaker"],
            deps["user"],
            file=file,
            category="kb",
            doc_type="rule",
            security_level="internal",
            version="1.0",
            keywords="",
            card_type=None,
            customer_tier=None,
            effective_date=None,
            expiry_date=None,
        )
    assert result["status"] == "COMPLETED"
