"""API 鉴权 — JWT 优先 + API Key 兜底 (I1-C2)

模式:
  1. 优先解析 Authorization: Bearer <jwt> (HS256, 含 sub/tenant_id/roles 等 claim)
  2. JWT 解析失败 / 未启用 → fallback 到 API Key 模式 (内部分服务互调用)

返回 Principal dataclass, 业务层通过依赖注入拿到完整身份:
  - actor_id: 审计 + 业务操作留痕
  - actor_role: 业务角色 (editor/reviewer/auditor/admin/service)
  - tenant_id: 多租户隔离
  - roles: 细粒度权限 (与 KbDocument.allowed_roles 配对)
  - auth_method: jwt / api_key (审计用, 防"用 API Key 当 JWT 用"的灰色场景)

生产级内部服务认证: 接入 JWT/OAuth/Kong 等网关层后, API Key 模式可下线.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Literal

import jwt
from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from kb.config import get_settings

_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    """调用方身份 (I1-C2)

    通过 Depends(verify_principal) 注入到业务端点.
    """

    actor_id: str
    actor_role: str
    tenant_id: str
    roles: list[str] = field(default_factory=list)
    tier: str = "normal"  # VIP/normal/internal (限流分层用)
    auth_method: Literal["jwt", "api_key"] = "jwt"


def _looks_like_jwt(token: str) -> bool:
    """JWT 是 xxxx.yyyy.zzzz 三段式, API Key 不会含两个点"""
    return token.count(".") == 2


def _verify_jwt(token: str) -> Principal:
    """校验 JWT, 返回 Principal

    校验项:
    - 签名 (HS256)
    - exp / iat 必须存在
    - audience 必须匹配 settings.jwt.audience
    - issuer 必须匹配 settings.jwt.issuer
    - leeway: 时钟偏移容差 (默认 30s)

    claim 映射:
    - sub → actor_id (必填, 缺则 401)
    - tenant_id → tenant_id (可选, 缺省用 default_tenant_id)
    - roles → roles list
    - actor_role → actor_role (缺省从 roles 推或用 "user")
    - tier → tier (缺省 normal)
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt.secret,
            algorithms=[settings.jwt.algorithm],
            audience=settings.jwt.audience,
            issuer=settings.jwt.issuer,
            leeway=settings.jwt.leeway_seconds,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="JWT 已过期")
    except jwt.InvalidAudienceError:
        raise HTTPException(status_code=401, detail="JWT audience 不匹配")
    except jwt.InvalidIssuerError:
        raise HTTPException(status_code=401, detail="JWT issuer 不匹配")
    except jwt.MissingRequiredClaimError as e:
        raise HTTPException(status_code=401, detail=f"JWT 缺必填 claim: {e.claim}")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"JWT 无效: {e}")

    actor_id = payload.get("sub")
    if not actor_id:
        raise HTTPException(status_code=401, detail="JWT claim sub (actor_id) 缺失")

    tenant_id = payload.get("tenant_id") or settings.security.default_tenant_id
    roles = payload.get("roles") or []
    if not isinstance(roles, list):
        raise HTTPException(status_code=401, detail="JWT claim roles 必须是 list")
    actor_role = payload.get("actor_role") or _infer_role(roles)
    tier = payload.get("tier") or "normal"

    return Principal(
        actor_id=str(actor_id),
        actor_role=str(actor_role),
        tenant_id=str(tenant_id),
        roles=[str(r) for r in roles],
        tier=str(tier),
        auth_method="jwt",
    )


def _infer_role(roles: list[str]) -> str:
    """从 roles 列表推断主角色 (审批/审计用)"""
    if "admin" in roles:
        return "admin"
    if "reviewer" in roles:
        return "reviewer"
    if "editor" in roles:
        return "editor"
    if "auditor" in roles:
        return "auditor"
    return "user"


def _verify_api_key(token: str) -> Principal:
    """校验 API Key (兜底模式, 内部服务互调用)

    actor_id 用 sha256(key)[:8] 而非明文 key (审计脱敏).
    """
    settings = get_settings()
    valid_keys = settings.api_keys_list
    if not valid_keys:
        if settings.environment == "development":
            # 开发环境: 无 Key 也能跑, 用 anonymous 身份
            return Principal(
                actor_id="anonymous-dev",
                actor_role="developer",
                tenant_id=settings.security.default_tenant_id,
                roles=["developer"],
                tier="internal",
                auth_method="api_key",
            )
        raise HTTPException(status_code=500, detail="API Keys 未配置，非开发环境拒绝所有请求")

    for valid_key in valid_keys:
        if hmac.compare_digest(token, valid_key):
            key_hash = hashlib.sha256(valid_key.encode()).hexdigest()[:8]
            return Principal(
                actor_id=f"api_key:{key_hash}",
                actor_role="service",
                tenant_id=settings.security.default_tenant_id,
                roles=["service"],
                tier="internal",
                auth_method="api_key",
            )

    raise HTTPException(status_code=403, detail="无效的 API Key")


async def verify_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> Principal:
    """统一鉴权入口: JWT 优先, API Key 兜底

    通过 Depends 注入到业务端点. request.state.principal 也存一份,
    给 AuditMiddleware / 业务逻辑统一访问.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="缺少 Authorization Bearer 头")

    token = credentials.credentials
    settings = get_settings()

    # JWT 模式
    if settings.jwt.enabled and _looks_like_jwt(token):
        try:
            principal = _verify_jwt(token)
        except HTTPException:
            # JWT 解析失败时, 如果 token 看起来像 API Key 也能走兜底
            if not _looks_like_api_key(token):
                raise
            principal = _verify_api_key(token)
    else:
        principal = _verify_api_key(token)

    # 注入到 request.state 供 AuditMiddleware / 业务层使用
    request.state.principal = principal
    return principal


def _looks_like_api_key(token: str) -> bool:
    """API Key 是高熵随机串, 不含两个连续的点 (区别于 JWT)"""
    return token.count(".") < 2


def require_role(*allowed_roles: str):
    """角色守卫装饰器工厂

    用法:
        @router.post(..., dependencies=[Depends(require_role("admin"))])

    不在 allowlist 的角色 → 403
    """
    from fastapi import Depends

    async def _check(principal: Principal = Depends(verify_principal)) -> Principal:
        if not any(r in principal.roles for r in allowed_roles):
            raise HTTPException(
                status_code=403,
                detail=f"需要角色 {list(allowed_roles)}, 实际 {principal.roles}",
            )
        return principal

    return _check
