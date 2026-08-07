"""JWT 认证与限流单元测试"""

from __future__ import annotations

import fastapi
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lumio.shared.auth import (
    AuthenticationError,
    AuthorizationError,
    AuthUser,
    create_access_token,
    decode_token,
    get_current_user,
    require_role,
)

# ── JWT 编解码 ──


def test_create_and_decode_token():
    """JWT token 创建后可正确解码"""
    token = create_access_token("user-001", "agent")
    payload = decode_token(token)
    assert payload["sub"] == "user-001"
    assert payload["role"] == "agent"
    assert "exp" in payload
    assert "iat" in payload


def test_create_token_with_session_id():
    """带 session_id 的 token 可正确解码"""
    token = create_access_token("cust-001", "customer", session_id="sess-123")
    payload = decode_token(token)
    assert payload["session_id"] == "sess-123"


def test_decode_invalid_token_raises():
    """无效 token 抛出 AuthenticationError"""
    with pytest.raises(AuthenticationError):
        decode_token("invalid.token.here")


def test_decode_expired_token_raises():
    """过期 token 抛出 AuthenticationError"""
    token = create_access_token("user-001", "agent", expires_minutes=-1)
    with pytest.raises(AuthenticationError, match="过期"):
        decode_token(token)


# ── RBAC 角色鉴权 ──


def test_require_role_allows_correct_role():
    """require_role 允许正确的角色"""
    check = require_role("agent", "admin")
    user = AuthUser(user_id="u1", role="agent")
    result = check(user)
    assert result.role == "agent"


def test_require_role_rejects_wrong_role():
    """require_role 拒绝错误的角色"""
    check = require_role("admin")
    user = AuthUser(user_id="u1", role="customer")
    with pytest.raises(AuthorizationError):
        check(user)


def test_require_role_multiple_allowed():
    """require_role 支持多角色"""
    check = require_role("customer", "agent")
    # customer 通过
    check(AuthUser(user_id="u1", role="customer"))
    # agent 通过
    check(AuthUser(user_id="u2", role="agent"))
    # admin 被拒
    with pytest.raises(AuthorizationError):
        check(AuthUser(user_id="u3", role="admin"))


# ── get_current_user 依赖注入 ──


async def test_get_current_user_with_valid_token():
    """带有效 Bearer token 的请求返回正确用户"""
    token = create_access_token("agent-001", "agent")

    app = FastAPI()

    @app.get("/test")
    async def test_endpoint(user=fastapi.Depends(get_current_user)):
        return {"user_id": user.user_id, "role": user.role}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/test", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "agent-001"
        assert data["role"] == "agent"


async def test_get_current_user_dev_mode_no_token(monkeypatch: pytest.MonkeyPatch):
    """P0-3 整改: dev 旁路需显式设 LUMIO_DEV_AUTH_BYPASS=true 才放行 admin"""
    monkeypatch.setenv("LUMIO_ENVIRONMENT", "development")
    monkeypatch.setenv("LUMIO_DEV_AUTH_BYPASS", "true")
    # 清 lru_cache 让 env 重新生效
    from lumio.shared.config import get_settings

    get_settings.cache_clear()
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint(user=fastapi.Depends(get_current_user)):
        return {"user_id": user.user_id, "role": user.role}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/test")
        assert resp.status_code == 200
        assert resp.json()["role"] == "admin"


async def test_get_current_user_dev_bypass_default_off(monkeypatch: pytest.MonkeyPatch):
    """P0-3 核心: 默认 dev 旁路关闭, 任何漏配 LUMIO_DEV_AUTH_BYPASS 的部署都需 token"""
    monkeypatch.setenv("LUMIO_ENVIRONMENT", "development")
    monkeypatch.delenv("LUMIO_DEV_AUTH_BYPASS", raising=False)
    from lumio.shared.config import get_settings

    get_settings.cache_clear()
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint(user=fastapi.Depends(get_current_user)):
        return {"user_id": user.user_id, "role": user.role}

    # dev_bypass 默认 False → 显式断言 AuthenticationError 被 raise
    # (FastAPI Depends 注入时 raise, 测试不挂 exception handler, 直接捕获)
    from lumio.shared.auth import AuthenticationError

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with pytest.raises(AuthenticationError, match="缺少 Authorization 头"):
            await client.get("/test")


async def test_get_current_user_token_via_query_param():
    """通过 query param 传 token（WebSocket 场景）"""
    token = create_access_token("cust-001", "customer")

    app = FastAPI()

    @app.get("/test")
    async def test_endpoint(user=fastapi.Depends(get_current_user)):
        return {"user_id": user.user_id, "role": user.role}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/test", params={"token": token})
        assert resp.status_code == 200
        assert resp.json()["role"] == "customer"
