"""Prompt 注册中心单元测试 (prompt_registry.py)"""

from __future__ import annotations

import json
import time

from lumio.services.bot.prompt_registry import (
    _LOCAL_PROMPTS,
    PromptRegistry,
    PromptVersion,
    get_prompt,
    get_prompt_registry,
)


def test_prompt_version_rollout_clamp():
    """rollout_pct 限制在 0~100"""
    assert PromptVersion("n", "v", "c", rollout_pct=150.0).rollout_pct == 100.0
    assert PromptVersion("n", "v", "c", rollout_pct=-10.0).rollout_pct == 0.0
    assert PromptVersion("n", "v", "c").rollout_pct == 100.0
    assert "rollout=100.0%" in repr(PromptVersion("n", "v", "c"))


def test_local_prompts_initialized():
    """本地兜底库含 3 个 prompt (实例化后初始化)"""
    PromptRegistry()  # 触发 _init_local_prompts
    assert set(_LOCAL_PROMPTS) == {"knowledge_agent", "business_agent", "fallback_agent"}
    for pv in _LOCAL_PROMPTS.values():
        assert pv.content  # 内容非空


def test_get_prompt_local_fallback():
    """无 Nacos/Redis 时兜底本地 prompt"""
    reg = PromptRegistry()
    content = reg.get_prompt("knowledge_agent")
    assert content == _LOCAL_PROMPTS["knowledge_agent"].content


def test_get_prompt_missing():
    """不存在的 prompt 返回占位符"""
    reg = PromptRegistry()
    assert reg.get_prompt("no_such_prompt") == "[PROMPT_MISSING:no_such_prompt]"


def test_get_prompt_from_redis_cache(monkeypatch):
    """Redis 缓存命中优先于本地"""
    reg = PromptRegistry()

    class _FakeRedisSync:
        def __init__(self, data: dict) -> None:
            self.data = data

        def get(self, key: str):
            return self.data.get(key)

    payload = json.dumps({"version": "v2", "content": "新版 prompt 内容", "rollout_pct": 100.0})
    reg._redis_client = _FakeRedisSync({"lumio:prompt:knowledge_agent": payload})
    content = reg.get_prompt("knowledge_agent")
    assert content == "新版 prompt 内容"
    assert reg._cache["knowledge_agent"].version == "v2"


def test_get_prompt_rollout_switch_back():
    """rollout_pct < 100 且未命中 → 切回主版本"""
    reg = PromptRegistry()
    now = time.time()
    reg._cache["knowledge_agent"] = PromptVersion(
        name="knowledge_agent",
        version="canary-v2",
        content="灰度内容",
        rollout_pct=1.0,  # 1% 灰度
    )
    reg._cache_loaded_at["knowledge_agent"] = now
    # 用 hash 不在 1% 内的 customer_id
    content = reg.get_prompt("knowledge_agent", customer_id="customer-x")
    assert content == _LOCAL_PROMPTS["knowledge_agent"].content  # 主版本


def test_get_prompt_rollout_hit():
    """rollout_pct < 100 且命中灰度 → 灰度内容"""
    reg = PromptRegistry()
    now = time.time()
    reg._cache["knowledge_agent"] = PromptVersion(
        name="knowledge_agent",
        version="canary-v2",
        content="灰度内容",
        rollout_pct=100.0,
    )
    reg._cache_loaded_at["knowledge_agent"] = now
    assert reg.get_prompt("knowledge_agent", customer_id="c") == "灰度内容"


def test_is_in_rollout_sticky():
    """粘性分桶: 同一 customer 结果稳定"""
    assert PromptRegistry._is_in_rollout("", 50.0) is True  # 空 customer 全量
    r1 = PromptRegistry._is_in_rollout("cust-1", 50.0)
    r2 = PromptRegistry._is_in_rollout("cust-1", 50.0)
    assert r1 == r2


def test_get_metadata():
    """元数据查询"""
    reg = PromptRegistry()
    meta = reg.get_metadata("knowledge_agent")
    assert meta["version"] == "local-v1"
    assert "changelog" in meta
    assert reg.get_metadata("no_such") is None


def test_get_metadata_from_cache():
    """缓存版本元数据优先"""
    reg = PromptRegistry()
    reg._cache["business_agent"] = PromptVersion(name="business_agent", version="v9", content="c", changelog="改")
    assert reg.get_metadata("business_agent")["version"] == "v9"


def test_invalidate_cache():
    """缓存清除: 单名与全量"""
    reg = PromptRegistry()
    reg._cache["a"] = PromptVersion("a", "v1", "c")
    reg._cache["b"] = PromptVersion("b", "v1", "c")
    reg._cache_loaded_at["a"] = time.time()

    reg.invalidate_cache("a")
    assert "a" not in reg._cache
    assert "b" in reg._cache

    reg.invalidate_cache()
    assert reg._cache == {}


def test_redis_error_soft():
    """Redis 读取异常 → 回退本地"""
    reg = PromptRegistry()

    class _BoomRedis:
        def get(self, key: str):
            raise RuntimeError("redis down")

    reg._redis_client = _BoomRedis()
    content = reg.get_prompt("knowledge_agent")
    assert content == _LOCAL_PROMPTS["knowledge_agent"].content


def test_registry_singleton_and_helper():
    """单例 + 便捷函数"""
    r1 = get_prompt_registry()
    r2 = get_prompt_registry()
    assert r1 is r2
    assert get_prompt("fallback_agent") == _LOCAL_PROMPTS["fallback_agent"].content
