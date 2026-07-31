"""Embedder 测试 — 熔断器状态机 + 工厂

不依赖真实嵌入服务, 通过 mock 验证 EmbeddingCircuitBreaker 状态转换.
"""

from __future__ import annotations

import pytest

from kb.pipeline.embedder import (
    BGE_QUERY_INSTRUCTION,
    EmbeddingCircuitBreaker,
    create_embedding_provider,
)


class FakeProvider:
    """测试用假 Provider, 手动控制 health_check 返回值"""

    def __init__(self, dim: int = 4, *, health_results: list[bool] | None = None) -> None:
        self._dim = dim
        self._health_results = list(health_results or [True])
        self.embed_calls = 0
        self.health_calls = 0

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return "fake"

    @property
    def query_instruction(self) -> str:
        return BGE_QUERY_INSTRUCTION

    async def embed(self, texts, *, instruction: str = ""):
        self.embed_calls += 1
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]

    async def embed_query(self, text: str):
        return [0.1, 0.2, 0.3, 0.4]

    async def health_check(self) -> bool:
        self.health_calls += 1
        if not self._health_results:
            return True
        return self._health_results.pop(0)


class TestCircuitBreakerInitialState:
    def test_starts_open_until_first_probe(self):
        provider = FakeProvider()
        cb = EmbeddingCircuitBreaker(provider, probe_interval=60, failure_threshold=3)
        # 初始状态: 熔断打开, 不可用
        assert cb.is_available is False


class TestCircuitBreakerRecovery:
    @pytest.mark.asyncio
    async def test_closes_after_recovery_threshold_successes(self):
        provider = FakeProvider(health_results=[True, True])
        cb = EmbeddingCircuitBreaker(
            provider, probe_interval=0, failure_threshold=3, recovery_threshold=2,
        )
        await cb._probe_once()
        await cb._probe_once()
        assert cb.is_available is True

    @pytest.mark.asyncio
    async def test_does_not_close_below_threshold(self):
        provider = FakeProvider(health_results=[True])  # 只 1 次成功
        cb = EmbeddingCircuitBreaker(
            provider, probe_interval=0, failure_threshold=3, recovery_threshold=2,
        )
        await cb._probe_once()
        # 1 次成功 < 阈值 2, 仍打开
        assert cb.is_available is False


class TestCircuitBreakerTripping:
    @pytest.mark.asyncio
    async def test_opens_after_consecutive_failures(self):
        provider = FakeProvider(health_results=[True, True])  # 前 2 次成功 (会关)
        cb = EmbeddingCircuitBreaker(
            provider, probe_interval=0, failure_threshold=2, recovery_threshold=2,
        )
        await cb._probe_once()
        await cb._probe_once()
        assert cb.is_available is True  # 已关闭

        # 接下来连续失败 2 次, 达到阈值, 再次打开
        provider._health_results = [False, False]
        await cb._probe_once()
        await cb._probe_once()
        assert cb.is_available is False  # 重新打开

    @pytest.mark.asyncio
    async def test_success_resets_failure_counter(self):
        # 失败 - 成功 - 失败: 不应触发 (因为中间的成功重置了计数)
        provider = FakeProvider(health_results=[False, True, False])
        cb = EmbeddingCircuitBreaker(
            provider, probe_interval=0, failure_threshold=2, recovery_threshold=2,
        )
        await cb._probe_once()  # 失败 (count=1)
        await cb._probe_once()  # 成功 (重置, count=0)
        await cb._probe_once()  # 失败 (count=1)
        # 计数从未到 2, 熔断器仍为初始状态
        assert cb._consecutive_failures == 1


class TestCircuitBreakerLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop_probe(self):
        provider = FakeProvider(health_results=[])
        cb = EmbeddingCircuitBreaker(provider, probe_interval=0.01, failure_threshold=3)
        await cb.start_probe()
        # 给探测循环一个 tick
        import asyncio
        await asyncio.sleep(0.05)
        await cb.stop_probe()
        # health_check 至少被调用了 1 次
        assert provider.health_calls >= 1

    @pytest.mark.asyncio
    async def test_stop_probe_idempotent(self):
        provider = FakeProvider()
        cb = EmbeddingCircuitBreaker(provider, probe_interval=1)
        await cb.start_probe()
        await cb.stop_probe()
        # 二次 stop 不应抛错
        await cb.stop_probe()


class TestCreateEmbeddingProvider:
    def test_ollama_provider(self):
        p = create_embedding_provider("ollama", ollama_base_url="http://x:1", ollama_model="m", dim=512)
        assert p.name == "m"
        assert p.dim == 512

    def test_tei_provider(self):
        p = create_embedding_provider("tei", tei_base_url="http://x:2", tei_model="bge", dim=1024)
        assert p.name == "bge"
        assert p.dim == 1024

    def test_unsupported_raises(self):
        with pytest.raises(ValueError, match="不支持的嵌入服务类型"):
            create_embedding_provider("openai")

    def test_tei_url_strips_trailing_slash(self):
        p = create_embedding_provider("tei", tei_base_url="http://x:2/", dim=128)
        # 通过 mock health 验证不会因末尾 / 报 404
        assert p._base_url == "http://x:2"
