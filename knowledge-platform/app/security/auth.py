"""API 认证 — Bearer API Key 模式

生产级内部服务认证：请求头 Authorization: Bearer <api_key>
API Key 从配置读取，生产环境应接入 JWT/OAuth/Kong 等网关层认证。
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

_bearer_scheme = HTTPBearer(auto_error=False)


async def verify_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> str:
    """验证 API Key

    从 Authorization: Bearer <key> 提取 key，与配置的 API Keys 比对。
    使用 hmac.compare_digest 防止时序攻击。

    开发环境 (environment=development) 且未配置 API Keys 时跳过认证。
    """
    settings = get_settings()

    # 开发环境且未配置 Key 时跳过
    valid_keys = settings.api_keys_list
    if not valid_keys and settings.environment == "development":
        return "dev"

    if not valid_keys:
        raise HTTPException(status_code=500, detail="API Keys 未配置，非开发环境拒绝所有请求")

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="缺少 Authorization Bearer 头")

    provided = credentials.credentials
    for valid_key in valid_keys:
        if hmac.compare_digest(provided, valid_key):
            return provided[:8] + "..."

    raise HTTPException(status_code=403, detail="无效的 API Key")
