"""依赖注入层单元测试 (deps.py init/close 成功与降级路径)"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI, Request
from starlette.requests import Request as StarletteRequest


def _make_request(app: FastAPI) -> Request:
    """构造带 app 的 Request (app 经 scope 注入)"""
    scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "app": app}
    return StarletteRequest(scope)  # type: ignore[return-value]


# ── init/close embedding ──


async def test_init_embedding_success():
    """嵌入服务初始化成功 (维度匹配)"""
    from lumio.services.common import deps

    app = FastAPI()
    fake_provider = MagicMock()
    fake_provider.embed = AsyncMock(return_value=[[0.1] * 1024])

    with patch.object(deps, "create_embedding_provider", return_value=fake_provider):
        await deps.init_embedding(app)
    assert app.state.embedding_provider is fake_provider
    assert app.state.embedding_breaker is not None
    # 清理探针
    await deps.close_embedding(app)


async def test_init_embedding_dim_mismatch_dev_degrades():
    """维度不匹配 + development 环境 → 降级不抛"""
    from lumio.services.common import deps

    app = FastAPI()
    fake_provider = MagicMock()
    fake_provider.embed = AsyncMock(return_value=[[0.1] * 8])  # 维度错误

    with patch.object(deps, "create_embedding_provider", return_value=fake_provider):
        await deps.init_embedding(app)
    assert app.state.embedding_provider is fake_provider  # 降级但已设置


async def test_close_embedding_no_breaker():
    """无熔断器时 close 不抛"""
    from lumio.services.common import deps

    app = FastAPI()
    await deps.close_embedding(app)


# ── init/close elasticsearch ──


async def test_init_es_success():
    """ES 连接成功"""
    from lumio.services.common import deps

    app = FastAPI()
    fake_es = AsyncMock()
    fake_es.ping = AsyncMock(return_value=True)
    with patch("elasticsearch.AsyncElasticsearch", return_value=fake_es):
        await deps.init_elasticsearch(app)
    assert app.state.es_client is fake_es
    await deps.close_elasticsearch(app)
    assert app.state.es_client is None


async def test_init_es_failure_degrades():
    """ES 连接失败 → None 降级"""
    from lumio.services.common import deps

    app = FastAPI()
    fake_es = AsyncMock()
    fake_es.ping = AsyncMock(side_effect=ConnectionError("down"))
    with patch("elasticsearch.AsyncElasticsearch", return_value=fake_es):
        await deps.init_elasticsearch(app)
    assert app.state.es_client is None


async def test_close_es_none():
    """无 client close 不抛"""
    from lumio.services.common import deps

    await deps.close_elasticsearch(FastAPI())


# ── init/close milvus ──


async def test_init_milvus_success():
    """Milvus 连接成功"""
    from lumio.services.common import deps

    app = FastAPI()
    fake_collection = MagicMock()
    fake_collection.load = MagicMock()
    with (
        patch("pymilvus.connections") as mock_conn,
        patch("pymilvus.Collection", return_value=fake_collection),
    ):
        await deps.init_milvus(app)
        assert app.state.milvus_collection is fake_collection
        await deps.close_milvus(app)  # 必须在 patch 上下文内 (函数内 import)
        mock_conn.disconnect.assert_called_once()
        assert app.state.milvus_collection is None


async def test_init_milvus_timeout_degrades():
    """Milvus 超时 → None 降级"""
    from lumio.services.common import deps

    app = FastAPI()
    fake_collection = MagicMock()

    def slow_load():
        raise TimeoutError("load timeout")

    fake_collection.load = slow_load
    with (
        patch("pymilvus.connections"),
        patch("pymilvus.Collection", return_value=fake_collection),
    ):
        await deps.init_milvus(app)
    assert app.state.milvus_collection is None


# ── init/close minio ──


async def test_init_minio_success():
    """MinIO 连接成功, bucket 不存在时创建"""
    from lumio.services.common import deps

    app = FastAPI()
    fake_client = MagicMock()
    fake_client.bucket_exists = MagicMock(return_value=False)
    fake_client.make_bucket = MagicMock()
    with patch("minio.Minio", return_value=fake_client):
        await deps.init_minio(app)
    assert app.state.minio_client is fake_client
    fake_client.make_bucket.assert_called_once()
    await deps.close_minio(app)
    assert app.state.minio_client is None


async def test_init_minio_failure_degrades():
    """MinIO 连接失败 → None 降级"""
    from lumio.services.common import deps

    app = FastAPI()
    fake_client = MagicMock()
    fake_client.bucket_exists = MagicMock(side_effect=RuntimeError("conn refused"))
    with patch("minio.Minio", return_value=fake_client):
        await deps.init_minio(app)
    assert app.state.minio_client is None


# ── init/close llm ──


async def test_init_llm_and_getter():
    """LLM 客户端初始化 + getter"""
    from lumio.services.common import deps

    app = FastAPI()
    app.state.llm_breaker = MagicMock()  # init_llm 读取
    with patch.object(deps, "LLMClient") as mock_cls:
        await deps.init_llm(app)
        mock_cls.assert_called_once()
    assert app.state.llm_client is not None
    req = _make_request(app)
    assert deps.get_llm_client(req) is app.state.llm_client
    await deps.close_llm(app)
    assert app.state.llm_client is None


# ── getter 们 ──


def test_getters_return_state():
    """各 getter 返回 app.state 对应对象"""
    from lumio.services.common import deps

    app = FastAPI()
    app.state.embedding_provider = "ep"
    app.state.embedding_breaker = "eb"
    app.state.reranker_provider = "rp"
    app.state.es_client = "es"
    app.state.milvus_collection = "mc"
    app.state.minio_client = "mio"
    app.state.health_monitor = "hm"
    app.state.degradation_manager = "dm"
    app.state.session_manager = "sm"
    app.state.classifier = "clf"
    app.state.transfer_checker = "tc"
    app.state.mcp_client = "mcp"
    app.state.agent = "agent"
    app.state.chat_svc_client = "csc"

    req = _make_request(app)
    assert deps.get_embedding_provider(req) == "ep"
    assert deps.get_embedding_breaker(req) == "eb"
    assert deps.get_reranker_provider(req) == "rp"
    assert deps.get_es_client(req) == "es"
    assert deps.get_milvus_collection(req) == "mc"
    assert deps.get_minio_client(req) == "mio"
    assert deps.get_health_monitor(req) == "hm"
    assert deps.get_degradation_manager(req) == "dm"
    assert deps.get_session_manager(req) == "sm"
    assert deps.get_classifier(req) == "clf"
    assert deps.get_transfer_checker(req) == "tc"
    assert deps.get_mcp_client(req) == "mcp"
    assert deps.get_agent(req) == "agent"
    assert deps.get_chat_svc_client(req) == "csc"


def test_get_redis_client():
    """Redis getter"""
    from lumio.services.common import deps

    app = FastAPI()
    app.state.redis_client = "redis-obj"
    req = _make_request(app)
    assert deps.get_redis_client(req) == "redis-obj"


# ── 其他 init ──


async def test_init_health_monitor_and_close():
    """健康监控初始化 + 关闭"""
    from lumio.services.common import deps

    app = FastAPI()
    app.state.llm_client = MagicMock()
    app.state.llm_breaker = MagicMock()
    app.state.redis_client = MagicMock()
    with patch.object(deps, "HealthMonitor") as mock_cls:
        mock_cls.return_value.start = AsyncMock()
        mock_cls.return_value.stop = AsyncMock()
        await deps.init_health_monitor(app)
        mock_cls.return_value.start.assert_awaited_once()
    assert app.state.health_monitor is not None
    await deps.close_health_monitor(app)
    mock_cls.return_value.stop.assert_awaited_once()


async def test_init_classifier():
    """分类器初始化"""
    from lumio.services.common import deps

    app = FastAPI()
    app.state.llm_client = MagicMock()
    with (
        patch.object(deps, "RuleClassifier"),
        patch.object(deps, "LLMClassifier"),
        patch.object(deps, "IntentClassifier"),
    ):
        await deps.init_classifier(app)
    assert app.state.classifier is not None
    await deps.close_classifier(app)
    assert app.state.classifier is None


async def test_init_transfer_checker():
    """转人工检查器初始化"""
    from lumio.services.common import deps

    app = FastAPI()
    with patch.object(deps, "TransferChecker"):
        await deps.init_transfer_checker(app)
    assert app.state.transfer_checker is not None


async def test_close_without_state():
    """state 缺失时 close 不抛"""
    from lumio.services.common import deps

    await deps.close_agent(FastAPI())
    await deps.close_mcp_client(FastAPI())
    await deps.close_chat_svc_client(FastAPI())
    await deps.close_reranker(FastAPI())
