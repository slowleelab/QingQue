"""客户画像学习单元测试 (customer_memory.py)"""

from __future__ import annotations

import json
import time

import pytest

from lumio.services.bot.customer_memory import (
    _conservative_risk,
    _demote_vip,
    apply_learned_profile,
    learn_customer_profile,
)

# 近 100 秒内更新过 → 无衰减
_FRESH_TS = time.time() - 100


class _FakeResult:
    def __init__(self, content: str) -> None:
        self._content = content

    def scalar(self) -> str:
        return self._content


class _FakeSession:
    def __init__(self, content: str) -> None:
        self._content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, stmt):
        return _FakeResult(self._content)


class _FakeRedis:
    def __init__(self, cached: str | None = None) -> None:
        self.cached = cached
        self.setex_calls: list[tuple] = []

    async def get(self, key: str) -> str | None:
        return self.cached

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.setex_calls.append((key, ttl, value))


def _monkeypatch_redis(monkeypatch: pytest.MonkeyPatch, redis: _FakeRedis) -> None:
    import lumio.services.common.redis_client as rc

    monkeypatch.setattr(rc, "get_redis_client", lambda: redis)


# ── learn_customer_profile ──


async def test_learn_caches_hit(monkeypatch):
    """缓存命中直接返回, 不查库"""
    cached = json.dumps({"vip_level": "vip"}, ensure_ascii=False)
    redis = _FakeRedis(cached=cached)
    _monkeypatch_redis(monkeypatch, redis)

    profiles = await learn_customer_profile("c1", lambda: _FakeSession(""))
    assert profiles == {"vip_level": "vip"}
    assert redis.setex_calls == []  # 未写缓存


async def test_learn_from_history(monkeypatch):
    """从历史对话推断卡种/VIP/风险"""
    _monkeypatch_redis(monkeypatch, _FakeRedis())
    content = "我是白金卡用户，想办分期，最近不敢用分期怕逾期"
    profiles = await learn_customer_profile("c1", lambda: _FakeSession(content))

    assert "platinum" in profiles.get("card_types", [])
    assert profiles["vip_level"] == "vip"
    assert profiles["risk_tolerance"] == "R2"  # 分期 +1, 怕逾期 -2 → -1


async def test_learn_empty_history(monkeypatch):
    """历史为空 → 返回空 dict"""
    _monkeypatch_redis(monkeypatch, _FakeRedis())
    profiles = await learn_customer_profile("c1", lambda: _FakeSession(""))
    assert profiles == {}


async def test_learn_writes_cache(monkeypatch):
    """计算后写缓存 (24h TTL)"""
    redis = _FakeRedis()
    _monkeypatch_redis(monkeypatch, redis)
    await learn_customer_profile("c1", lambda: _FakeSession("我是钻石卡"))
    assert len(redis.setex_calls) == 1
    key, ttl, _ = redis.setex_calls[0]
    assert key == "lumio:profile:cache:c1"
    assert ttl == 86400


async def test_learn_redis_unavailable(monkeypatch):
    """Redis 不可用时直接计算 (降级)"""
    import lumio.services.common.redis_client as rc

    def boom():
        raise RuntimeError("no redis")

    monkeypatch.setattr(rc, "get_redis_client", boom)
    profiles = await learn_customer_profile("c1", lambda: _FakeSession("我是金卡"))
    assert "gold" in profiles.get("card_types", [])


# ── apply_learned_profile ──


class _FakeState:
    def __init__(
        self,
        *,
        version: int = 1,
        card_types=None,
        vip_level=None,
        risk_tolerance=None,
        updated_at: float = 0.0,
    ) -> None:
        self.version = version
        self.card_types = card_types
        self.vip_level = vip_level
        self.risk_tolerance = risk_tolerance
        self.vip_level_updated_at = updated_at
        self.risk_tolerance_updated_at = updated_at
        self.card_types_updated_at = updated_at


class _FakeSessionManager:
    def __init__(self, state: _FakeState) -> None:
        self.state = state
        self.patch_calls: list[dict] = []

    async def get_session(self, session_id: str):
        return self.state

    async def patch_state(self, **kwargs) -> None:
        self.patch_calls.append(kwargs)


async def test_apply_profile_fresh(monkeypatch):
    """画像新鲜时全部应用"""
    _monkeypatch_redis(monkeypatch, _FakeRedis())
    sm = _FakeSessionManager(_FakeState(updated_at=_FRESH_TS))  # 近期更新过
    content = "我是白金卡，想办分期"
    ok = await apply_learned_profile("c1", "s1", lambda: _FakeSession(content), sm)

    assert ok is True
    assert len(sm.patch_calls) == 1
    patches = sm.patch_calls[0]["patches"]
    # "白金卡" 同时命中 白金卡→platinum 与 金卡→gold 两个模式
    assert "platinum" in patches["card_types"]
    assert patches["vip_level"] == "vip"
    assert "risk_tolerance" in patches  # 分期 +1 → R3


async def test_apply_profile_decay_demotes_vip(monkeypatch):
    """VIP 长期未更新 → 衰减降级"""
    _monkeypatch_redis(monkeypatch, _FakeRedis())
    sm = _FakeSessionManager(_FakeState(updated_at=0))  # 从未更新 → 衰减 0
    content = "我是白金卡"
    ok = await apply_learned_profile("c1", "s1", lambda: _FakeSession(content), sm)

    assert ok is True
    patches = sm.patch_calls[0]["patches"]
    # 衰减 < 0.5 → 降级: 英文 vip 不在降级表 → 普通
    assert patches["vip_level"] == "普通"


async def test_apply_profile_existing_value_not_overwritten(monkeypatch):
    """会话已有显式 VIP → 衰减画像不覆盖"""
    _monkeypatch_redis(monkeypatch, _FakeRedis())
    sm = _FakeSessionManager(_FakeState(vip_level="私银", card_types=["diamond"], updated_at=0))
    content = "我是白金卡"
    ok = await apply_learned_profile("c1", "s1", lambda: _FakeSession(content), sm)

    # vip 已有显式值 → 不覆盖; card 已有值 → 不覆盖; 全部衰减 0 → 无 patch
    assert ok is False
    assert sm.patch_calls == []


async def test_apply_profile_no_profiles(monkeypatch):
    """无画像时返回 False"""
    _monkeypatch_redis(monkeypatch, _FakeRedis())
    sm = _FakeSessionManager(_FakeState())
    ok = await apply_learned_profile("c1", "s1", lambda: _FakeSession(""), sm)
    assert ok is False


async def test_apply_profile_session_missing(monkeypatch):
    """会话不存在时返回 False"""
    _monkeypatch_redis(monkeypatch, _FakeRedis())

    class _NoSessionSM:
        async def get_session(self, session_id: str):
            return None

    ok = await apply_learned_profile("c1", "s1", lambda: _FakeSession("我是白金卡"), _NoSessionSM())
    assert ok is False


async def test_apply_profile_exception_soft(monkeypatch):
    """内部异常不影响主流程 (返回 False)"""
    _monkeypatch_redis(monkeypatch, _FakeRedis())

    class _BoomSM:
        async def get_session(self, session_id: str):
            raise RuntimeError("boom")

    ok = await apply_learned_profile("c1", "s1", lambda: _FakeSession("我是白金卡"), _BoomSM())
    assert ok is False


# ── 降级映射纯函数 ──


def test_demote_vip_chain():
    """VIP 逐级降: 钻石→金→银→普通"""
    assert _demote_vip("钻石") == "金"
    assert _demote_vip("金") == "银"
    assert _demote_vip("银") == "普通"
    assert _demote_vip("普通") == "普通"
    assert _demote_vip("未知") == "普通"  # 未知一律兜底普通


def test_conservative_risk_chain():
    """风险保守化: R4→R3→R2→R1"""
    assert _conservative_risk("R4") == "R3"
    assert _conservative_risk("R3") == "R2"
    assert _conservative_risk("R2") == "R1"
    assert _conservative_risk("R1") == "R1"
    assert _conservative_risk("未知") == "R2"
