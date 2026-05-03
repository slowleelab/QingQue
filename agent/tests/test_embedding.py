"""嵌入服务单元测试"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from smartcs.services.common.embedding import (
    BGE_QUERY_INSTRUCTION,
    EmbeddingCircuitBreaker,
    EmbeddingProvider,
    OllamaEmbedding,
    TEIEmbedding,
    create_embedding_provider,
)
from smartcs.shared.exceptions import EmbeddingServiceError, EmbeddingTimeoutError

# ── 常量 ──


def test_bge_query_instruction() -> None:
    """BGE 查询指令常量应匹配预期值"""
    assert BGE_QUERY_INSTRUCTION == "为这个句子生成表示以用于检索相关文章："


# ── OllamaEmbedding ──


@pytest.mark.asyncio
async def test_ollama_embed() -> None:
    """OllamaEmbedding.embed() 应返回嵌入向量列表"""
    provider = OllamaEmbedding(
        base_url="http://localhost:11434",
        model="mxbai-embed-large",
        dim=1024,
        timeout=10.0,
        max_retries=2,
    )
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"embeddings": [[0.1] * 1024, [0.2] * 1024]}

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("smartcs.services.common.embedding.httpx.AsyncClient", return_value=mock_client):
        result = await provider.embed(["hello", "world"])

    assert len(result) == 2
    assert len(result[0]) == 1024
    assert result[0][0] == pytest.approx(0.1)
    assert result[1][0] == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_ollama_embed_query_adds_instruction() -> None:
    """OllamaEmbedding.embed_query() 应在文本前添加查询指令"""
    provider = OllamaEmbedding(
        base_url="http://localhost:11434",
        model="mxbai-embed-large",
        dim=1024,
        timeout=10.0,
        max_retries=2,
    )
    captured_input: list[str] = []

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"embeddings": [[0.1] * 1024]}

    mock_client = AsyncMock(spec=httpx.AsyncClient)

    async def fake_post(url: str, json: dict) -> MagicMock:
        captured_input.extend(json["input"])
        return mock_response

    mock_client.post = fake_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("smartcs.services.common.embedding.httpx.AsyncClient", return_value=mock_client):
        result = await provider.embed_query("信用卡年费")

    assert len(result) == 1024
    assert len(captured_input) == 1
    assert captured_input[0].startswith(BGE_QUERY_INSTRUCTION)
    assert "信用卡年费" in captured_input[0]


@pytest.mark.asyncio
async def test_ollama_embed_timeout_raises() -> None:
    """OllamaEmbedding.embed() 超时应抛出 EmbeddingTimeoutError"""
    provider = OllamaEmbedding(
        base_url="http://localhost:11434",
        model="mxbai-embed-large",
        dim=1024,
        timeout=1.0,
        max_retries=0,
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("smartcs.services.common.embedding.httpx.AsyncClient", return_value=mock_client),
        pytest.raises(EmbeddingTimeoutError),
    ):
        await provider.embed(["test"])


@pytest.mark.asyncio
async def test_ollama_embed_http_error_raises() -> None:
    """OllamaEmbedding.embed() HTTP 错误应抛出 EmbeddingServiceError"""
    provider = OllamaEmbedding(
        base_url="http://localhost:11434",
        model="mxbai-embed-large",
        dim=1024,
        timeout=10.0,
        max_retries=0,
    )
    mock_request = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 500

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(
        side_effect=httpx.HTTPStatusError("error", request=mock_request, response=mock_response)
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("smartcs.services.common.embedding.httpx.AsyncClient", return_value=mock_client),
        pytest.raises(EmbeddingServiceError),
    ):
        await provider.embed(["test"])


@pytest.mark.asyncio
async def test_ollama_embed_empty_response_raises() -> None:
    """OllamaEmbedding.embed() 空响应应抛出 EmbeddingServiceError"""
    provider = OllamaEmbedding(
        base_url="http://localhost:11434",
        model="mxbai-embed-large",
        dim=1024,
        timeout=10.0,
        max_retries=0,
    )
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"embeddings": []}

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("smartcs.services.common.embedding.httpx.AsyncClient", return_value=mock_client),
        pytest.raises(EmbeddingServiceError),
    ):
        await provider.embed(["test"])


@pytest.mark.asyncio
async def test_ollama_health_check() -> None:
    """OllamaEmbedding.health_check() 应正常工作"""
    provider = OllamaEmbedding(
        base_url="http://localhost:11434",
        model="mxbai-embed-large",
        dim=1024,
        timeout=10.0,
        max_retries=2,
    )
    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("smartcs.services.common.embedding.httpx.AsyncClient", return_value=mock_client):
        result = await provider.health_check()

    assert result is True


# ── TEIEmbedding ──


@pytest.mark.asyncio
async def test_tei_embed() -> None:
    """TEIEmbedding.embed() 应返回嵌入向量列表"""
    provider = TEIEmbedding(
        base_url="http://localhost:8080",
        model="BAAI/bge-M3",
        dim=1024,
        batch_size=128,
        timeout=10.0,
        max_retries=2,
    )
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [[0.3] * 1024, [0.4] * 1024]

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("smartcs.services.common.embedding.httpx.AsyncClient", return_value=mock_client):
        result = await provider.embed(["hello", "world"])

    assert len(result) == 2
    assert len(result[0]) == 1024
    assert result[0][0] == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_tei_embed_batching() -> None:
    """TEIEmbedding.embed() 应按 batch_size 分批请求"""
    provider = TEIEmbedding(
        base_url="http://localhost:8080",
        model="BAAI/bge-M3",
        dim=1024,
        batch_size=2,
        timeout=10.0,
        max_retries=2,
    )
    call_count = 0

    mock_client = AsyncMock(spec=httpx.AsyncClient)

    async def fake_post(url: str, json: dict) -> MagicMock:
        nonlocal call_count
        call_count += 1
        batch_size = len(json["inputs"])
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [[float(call_count * 10 + i)] * 1024 for i in range(batch_size)]
        return mock_response

    mock_client.post = fake_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("smartcs.services.common.embedding.httpx.AsyncClient", return_value=mock_client):
        result = await provider.embed(["a", "b", "c", "d", "e"])

    # 5 items with batch_size=2 => ceil(5/2) = 3 calls
    assert call_count == 3
    assert len(result) == 5


@pytest.mark.asyncio
async def test_tei_embed_query_adds_instruction() -> None:
    """TEIEmbedding.embed_query() 应在文本前添加查询指令"""
    provider = TEIEmbedding(
        base_url="http://localhost:8080",
        model="BAAI/bge-M3",
        dim=1024,
        batch_size=128,
        timeout=10.0,
        max_retries=2,
    )
    captured_input: list[str] = []

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [[0.1] * 1024]

    mock_client = AsyncMock(spec=httpx.AsyncClient)

    async def fake_post(url: str, json: dict) -> MagicMock:
        captured_input.extend(json["inputs"])
        return mock_response

    mock_client.post = fake_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("smartcs.services.common.embedding.httpx.AsyncClient", return_value=mock_client):
        await provider.embed_query("信用卡年费")

    assert len(captured_input) == 1
    assert captured_input[0].startswith(BGE_QUERY_INSTRUCTION)


# ── Provider 属性 ──


def test_provider_properties() -> None:
    """各 provider 的 dim/name/query_instruction 属性应正确返回"""
    ollama = OllamaEmbedding(
        base_url="http://localhost:11434",
        model="mxbai-embed-large",
        dim=1024,
        timeout=10.0,
        max_retries=2,
    )
    assert ollama.dim == 1024
    assert ollama.name == "mxbai-embed-large"
    assert ollama.query_instruction == BGE_QUERY_INSTRUCTION

    tei = TEIEmbedding(
        base_url="http://localhost:8080",
        model="BAAI/bge-M3",
        dim=1024,
        batch_size=64,
        timeout=10.0,
        max_retries=2,
    )
    assert tei.dim == 1024
    assert tei.name == "BAAI/bge-M3"
    assert tei.query_instruction == BGE_QUERY_INSTRUCTION


def test_provider_protocol_ollama() -> None:
    """OllamaEmbedding 应满足 EmbeddingProvider Protocol"""
    ollama = OllamaEmbedding(
        base_url="http://localhost:11434",
        model="mxbai-embed-large",
        dim=1024,
        timeout=10.0,
        max_retries=2,
    )
    assert isinstance(ollama, EmbeddingProvider)


def test_provider_protocol_tei() -> None:
    """TEIEmbedding 应满足 EmbeddingProvider Protocol"""
    tei = TEIEmbedding(
        base_url="http://localhost:8080",
        model="BAAI/bge-M3",
        dim=1024,
        batch_size=64,
        timeout=10.0,
        max_retries=2,
    )
    assert isinstance(tei, EmbeddingProvider)


# ── 工厂函数 ──


def test_create_embedding_provider_ollama() -> None:
    """create_embedding_provider('ollama') 应返回 OllamaEmbedding 实例"""
    provider = create_embedding_provider(
        provider_type="ollama",
        ollama_base_url="http://localhost:11434",
        ollama_model="mxbai-embed-large",
        tei_base_url="http://localhost:8080",
        tei_model="BAAI/bge-M3",
        dim=1024,
        batch_size=128,
        timeout=10.0,
        max_retries=2,
    )
    assert isinstance(provider, OllamaEmbedding)


def test_create_embedding_provider_tei() -> None:
    """create_embedding_provider('tei') 应返回 TEIEmbedding 实例"""
    provider = create_embedding_provider(
        provider_type="tei",
        ollama_base_url="http://localhost:11434",
        ollama_model="mxbai-embed-large",
        tei_base_url="http://localhost:8080",
        tei_model="BAAI/bge-M3",
        dim=1024,
        batch_size=128,
        timeout=10.0,
        max_retries=2,
    )
    assert isinstance(provider, TEIEmbedding)


# ── Circuit Breaker ──


@pytest.mark.asyncio
async def test_circuit_breaker_opens_on_failures() -> None:
    """连续失败达到阈值后熔断器应打开"""
    mock_provider = AsyncMock(spec=EmbeddingProvider)
    mock_provider.health_check = AsyncMock(return_value=False)

    cb = EmbeddingCircuitBreaker(mock_provider, probe_interval=0.1, failure_threshold=3, recovery_threshold=2)

    # 模拟连续失败
    for _ in range(3):
        await cb._probe_once()

    assert not cb.is_available


@pytest.mark.asyncio
async def test_circuit_breaker_closes_on_successes() -> None:
    """熔断器打开后连续成功达到阈值应关闭"""
    mock_provider = AsyncMock(spec=EmbeddingProvider)

    # 先失败 3 次打开熔断器
    mock_provider.health_check = AsyncMock(return_value=False)
    cb = EmbeddingCircuitBreaker(mock_provider, probe_interval=0.1, failure_threshold=3, recovery_threshold=2)

    for _ in range(3):
        await cb._probe_once()
    assert not cb.is_available

    # 再成功 2 次关闭熔断器
    mock_provider.health_check = AsyncMock(return_value=True)
    for _ in range(2):
        await cb._probe_once()
    assert cb.is_available
