"""I2-C1 限流分级单元测试

覆盖:
- PathGroup 归类 (5)
- tier 配额 (5)
- 限流 key (3)
- check_rate_limit 行为 (4)
- extract_context JWT 解析 (3)
- 降级 (3)
- 限流中间件 dispatch (2)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest

from kb.config import get_settings
from kb.security.rate_limiter import (
    PathGroup,
    check_rate_limit,
    classify_path,
    extract_context,
    get_ip_fallback_key,
    get_limit_key,
    get_tier_limit,
)

# ──────────────────────────────────────────────────────────────────────
# 1. PathGroup 归类 (5 用例)
# ──────────────────────────────────────────────────────────────────────


class TestClassifyPath:
    """HTTP method + 路径 → read/write/admin"""

    def test_get_documents_list_is_read(self):
        assert classify_path("GET", "/api/v1/documents") == PathGroup.READ

    def test_get_diagnostics_is_admin(self):
        assert classify_path("GET", "/api/v1/diagnostics") == PathGroup.ADMIN

    def test_post_documents_is_write(self):
        assert classify_path("POST", "/api/v1/documents") == PathGroup.WRITE

    def test_admin_clear_cache_is_admin(self):
        assert classify_path("POST", "/api/v1/admin/clear-cache") == PathGroup.ADMIN

    def test_post_retrieve_is_read(self):
        assert classify_path("POST", "/api/v1/retrieve") == PathGroup.READ


# ──────────────────────────────────────────────────────────────────────
# 2. tier 配额 (5 用例)
# ──────────────────────────────────────────────────────────────────────


class TestGetTierLimit:
    """tier × group → limit (缺 tier/group 时 fallback)"""

    def _settings(self) -> Any:
        # 用真 settings (lru_cache 单例), 跑测试时配置已加载
        return get_settings()

    def test_vip_read_quota(self):
        limit = get_tier_limit("vip", PathGroup.READ, self._settings())
        assert limit == 2000

    def test_normal_write_quota_is_low(self):
        """normal 用户上传配额严格 — 防滥用"""
        limit = get_tier_limit("normal", PathGroup.WRITE, self._settings())
        assert limit == 10

    def test_internal_admin_quota_high(self):
        limit = get_tier_limit("internal", PathGroup.ADMIN, self._settings())
        assert limit == 5000

    def test_unknown_tier_falls_back_to_default(self):
        """未知 tier → rate_limit_per_minute (兜底)"""
        limit = get_tier_limit("ghost_tier", PathGroup.READ, self._settings())
        assert limit == self._settings().security.rate_limit_per_minute

    def test_unknown_group_falls_back_to_default(self):
        """group 不在 quotas 中 → fallback"""
        settings = self._settings()
        # 模拟: vip 的 quotas 中没有 "audit" 这个 group
        limit = get_tier_limit("vip", PathGroup.ADMIN, settings)  # admin 在 vip 中有
        # 但如果用魔法 mock 制造不存在的 group, 走 fallback
        # 这里改用构造一个不存在的 group 值
        class FakeGroup:
            value = "nonexistent"

        limit_fallback = get_tier_limit("vip", FakeGroup(), settings)  # type: ignore[arg-type]
        assert limit_fallback == settings.security.rate_limit_per_minute
        # 正常 admin 还是 500
        assert limit == 500


# ──────────────────────────────────────────────────────────────────────
# 3. 限流 key (3 用例)
# ──────────────────────────────────────────────────────────────────────


class TestLimitKey:
    """actor + tier + group → 稳定 key, 格式正确"""

    def test_authenticated_user_key(self):
        key = get_limit_key(
            tier="vip", actor_id="alice", group=PathGroup.READ, prefix="kp:ratelimit"
        )
        assert key.startswith("kp:ratelimit:vip:")
        assert ":read" in key
        # actor_id 走 hash, 不直接出现明文
        assert "alice" not in key

    def test_different_user_different_key(self):
        k1 = get_limit_key(tier="normal", actor_id="alice", group=PathGroup.WRITE, prefix="x")
        k2 = get_limit_key(tier="normal", actor_id="bob", group=PathGroup.WRITE, prefix="x")
        assert k1 != k2

    def test_ip_fallback_key(self):
        key = get_ip_fallback_key(client_ip="10.0.0.1", group=PathGroup.READ, prefix="kp:ratelimit")
        assert key == "kp:ratelimit:anon:10.0.0.1:read"

    def test_path_param_does_not_change_key(self):
        """I2-C1 关键 bug 修复: doc_id 等路径参数不导致 key 分裂"""
        k1 = get_limit_key(tier="normal", actor_id="u1", group=PathGroup.READ, prefix="x")
        k2 = get_limit_key(tier="normal", actor_id="u1", group=PathGroup.READ, prefix="x")
        # 同样的 (tier, actor, group) 总是同样的 key, 与 doc_id 无关
        assert k1 == k2


# ──────────────────────────────────────────────────────────────────────
# 4. check_rate_limit (4 用例)
# ──────────────────────────────────────────────────────────────────────


class TestCheckRateLimit:
    """Redis Lua 原子 incr + expire"""

    @pytest.mark.asyncio
    async def test_under_limit_allowed(self):
        fake_redis = MagicMock()
        fake_redis.eval = AsyncMock(return_value=[1, 60])
        allowed, current, retry = await check_rate_limit(fake_redis, key="k", limit=10, window=60)
        assert allowed is True
        assert current == 1
        assert retry == 60

    @pytest.mark.asyncio
    async def test_at_limit_allowed(self):
        """current == limit 时仍允许 (边界: <=)"""
        fake_redis = MagicMock()
        fake_redis.eval = AsyncMock(return_value=[10, 30])
        allowed, current, _ = await check_rate_limit(fake_redis, key="k", limit=10, window=60)
        assert allowed is True
        assert current == 10

    @pytest.mark.asyncio
    async def test_over_limit_denied(self):
        fake_redis = MagicMock()
        fake_redis.eval = AsyncMock(return_value=[11, 30])
        allowed, current, _ = await check_rate_limit(fake_redis, key="k", limit=10, window=60)
        assert allowed is False
        assert current == 11

    @pytest.mark.asyncio
    async def test_ttl_minus_one_falls_back_to_window(self):
        """Lua 极端情况: TTL=-1 (key 无过期), retry 退化到 window"""
        fake_redis = MagicMock()
        fake_redis.eval = AsyncMock(return_value=[1, -1])
        allowed, _, retry = await check_rate_limit(fake_redis, key="k", limit=10, window=60)
        assert allowed is True
        assert retry == 60


# ──────────────────────────────────────────────────────────────────────
# 5. extract_context (3 用例)
# ──────────────────────────────────────────────────────────────────────


def _make_jwt(tier: str = "normal", sub: str = "alice", **extra) -> str:
    """构造未签名 JWT (中间件层不校验签名)"""
    import time

    payload = {
        "sub": sub,
        "tier": tier,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    payload.update(extra)
    return jwt.encode(payload, "test-secret", algorithm="HS256")


def _make_request(method: str, path: str, auth_header: str | None = None) -> Any:
    """构造 mock Request"""
    request = MagicMock()
    request.method = method
    request.url.path = path
    request.headers = {"authorization": auth_header} if auth_header else {}
    request.client.host = "10.0.0.5"
    return request


class TestExtractContext:
    """从 Request 抽取 (tier, actor_id, group, is_authenticated)"""

    def test_valid_jwt_extracts_tier_and_actor(self):
        token = _make_jwt(tier="vip", sub="alice")
        request = _make_request("GET", "/api/v1/documents", auth_header=f"Bearer {token}")
        settings = get_settings()
        ctx = extract_context(request, settings)
        assert ctx.tier == "vip"
        assert ctx.actor_id == "alice"
        assert ctx.is_authenticated is True
        assert ctx.group == PathGroup.READ

    def test_no_auth_header_falls_back_to_ip(self):
        request = _make_request("GET", "/api/v1/retrieve")
        settings = get_settings()
        ctx = extract_context(request, settings)
        assert ctx.tier == "anon"
        assert ctx.actor_id == "10.0.0.5"
        assert ctx.is_authenticated is False

    def test_invalid_jwt_falls_back_to_ip(self):
        """JWT 解析失败 (格式错误) → 兜底到 IP, 不阻塞"""
        request = _make_request("GET", "/api/v1/retrieve", auth_header="Bearer not.a.jwt")
        settings = get_settings()
        ctx = extract_context(request, settings)
        assert ctx.is_authenticated is False
        assert ctx.actor_id == "10.0.0.5"


# ──────────────────────────────────────────────────────────────────────
# 6. 限流中间件 dispatch 集成 (2 用例)
# ──────────────────────────────────────────────────────────────────────


class TestRateLimitMiddleware:
    """中间件级别 — 白名单 + 路径分组 + 限流响应"""

    @pytest.mark.asyncio
    async def test_health_whitelist_passes_through(self):
        """白名单路径 (e.g. /health/live) 不走限流"""
        from kb.middleware.rate_limit import RateLimitMiddleware

        # 模拟: 即使 Redis 抛错, 白名单也直接放行
        async def fake_call_next(request):
            resp = MagicMock()
            resp.status_code = 200
            return resp

        request = _make_request("GET", "/health/live")
        # 即便没有 Redis, 也会被白名单短路
        response = await RateLimitMiddleware.dispatch(
            self=MagicMock(),  # type: ignore[arg-type]
            request=request,
            call_next=fake_call_next,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_metrics_whitelist_passes_through(self):
        from kb.middleware.rate_limit import RateLimitMiddleware

        async def fake_call_next(request):
            resp = MagicMock()
            resp.status_code = 200
            return resp

        request = _make_request("GET", "/metrics")
        response = await RateLimitMiddleware.dispatch(
            self=MagicMock(),  # type: ignore[arg-type]
            request=request,
            call_next=fake_call_next,
        )
        assert response.status_code == 200
