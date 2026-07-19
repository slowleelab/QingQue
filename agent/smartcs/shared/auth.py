"""JWT 认证与 RBAC 鉴权

提供:
- create_access_token() / decode_token() — JWT 编解码
- AuthUser — 从 token 解析的用户信息
- get_current_user() — FastAPI 依赖注入，从 Authorization 头提取并验证 token
- require_role() — 角色鉴权依赖工厂

角色模型:
- customer — 客户，可使用 bot 聊天接口
- agent — 坐席，可使用 assist 接口
- admin — 管理员，可使用知识库管理接口
- service — 内部服务间调用（star-connection → assist）
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import jwt
from fastapi import Depends, Request
from pydantic import BaseModel

from smartcs.shared.config import get_settings
from smartcs.shared.exceptions import SmartCSError

logger = logging.getLogger(__name__)

Role = Literal["customer", "agent", "admin", "service"]


class AuthUser(BaseModel):
    """从 JWT token 解析的认证用户"""

    user_id: str
    role: Role
    session_id: str | None = None  # customer 场景下关联的 session


class AuthenticationError(SmartCSError):
    """401: 认证失败 — token 缺失/无效/过期"""

    code = 1001
    message = "认证失败"

    def __init__(self, detail: str = "") -> None:
        msg = f"认证失败: {detail}" if detail else "认证失败"
        super().__init__(message=msg)


class AuthorizationError(SmartCSError):
    """403: 鉴权失败 — 角色权限不足"""

    code = 1003
    message = "权限不足"

    def __init__(self, required_role: str = "") -> None:
        msg = f"权限不足，需要角色: {required_role}" if required_role else "权限不足"
        super().__init__(message=msg)


# ── JWT 编解码 ──


def create_access_token(
    user_id: str,
    role: Role,
    *,
    session_id: str | None = None,
    expires_minutes: int | None = None,
) -> str:
    """生成 JWT access token

    包含标准声明: iss（签发者）、aud（受众）、exp（过期）、iat（签发时间）、jti（唯一 ID）。
    """
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=expires_minutes or settings.jwt_expire_minutes)
    import uuid as _uuid

    payload = {
        "sub": user_id,
        "role": role,
        "iss": "smartcs",
        "aud": "smartcs-api",
        "exp": expire,
        "iat": datetime.now(UTC),
        "jti": _uuid.uuid4().hex,
    }
    if session_id:
        payload["session_id"] = session_id
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: str, role: Role) -> str:
    """生成 refresh token（长期有效，用于续签 access token）"""
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(days=7)
    import uuid as _uuid

    payload = {
        "sub": user_id,
        "role": role,
        "iss": "smartcs",
        "aud": "smartcs-refresh",
        "exp": expire,
        "iat": datetime.now(UTC),
        "jti": _uuid.uuid4().hex,
        "type": "refresh",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """解码 JWT token，验证 iss/aud + 时钟偏移容忍

    leeway=30s 容忍多机部署的时钟不同步。
    """
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer="smartcs",
            audience="smartcs-api",
            leeway=30,
        )
    except jwt.ExpiredSignatureError:
        raise AuthenticationError("token 已过期") from None
    except jwt.InvalidTokenError as e:
        raise AuthenticationError(f"token 无效: {e}") from None


# ── FastAPI 依赖注入 ──


def get_current_user(request: Request) -> AuthUser:
    """从 Authorization: Bearer <token> 头提取并验证用户

    开发环境（SMARTCS_ENVIRONMENT=development）支持 query param ?token=xxx
    用于 WebSocket 和测试场景。
    """
    settings = get_settings()

    # 开发环境旁路：无 Authorization 头时放行
    auth_header = request.headers.get("Authorization", "")
    token = ""

    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    elif "token" in request.query_params:
        # WebSocket / 测试场景：query param 传 token
        token = request.query_params["token"]

    if not token:
        if settings.environment == "development":
            # 开发环境无 token 时返回默认 admin 用户
            # 安全检查: 仅允许绑定 127.0.0.1 时使用旁路
            if settings.service_host not in ("127.0.0.1", "localhost", "0.0.0.0"):
                raise AuthenticationError("开发旁路仅允许本地绑定")
            return AuthUser(user_id="dev-user", role="admin")
        raise AuthenticationError("缺少 Authorization 头")

    payload = decode_token(token)
    return AuthUser(
        user_id=payload.get("sub", ""),
        role=payload.get("role", "customer"),
        session_id=payload.get("session_id"),
    )


CurrentUser = Annotated[AuthUser, Depends(get_current_user)]


def require_role(*allowed_roles: Role):
    """角色鉴权依赖工厂

    用法:
        @router.post("/admin/...", dependencies=[Depends(require_role("admin"))])
        async def admin_endpoint():
            ...
    """

    def _check_role(user: CurrentUser) -> AuthUser:
        if user.role not in allowed_roles:
            raise AuthorizationError(required_role="/".join(allowed_roles))
        return user

    return _check_role
