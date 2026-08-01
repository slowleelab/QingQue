"""I2-C1 限流分级 — tier 配额 + path group

tier 维度: vip / normal / internal (Principal.tier)
path 维度: read / write / admin (按 HTTP method + 路径前缀归类)

设计要点:
- tier 配额由 settings.security.tier_quotas 配置, fallback 到 rate_limit_per_minute
- Redis Lua 脚本原子完成: incr + expire + 返回当前计数
- 限流 key 含 tier + actor + path_group, 不再用 path 路径段 (避免每 doc_id 独立桶)
- JWT 在中间件层 best-effort 解析, 失败/未认证走 IP 兜底
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any

import jwt
from starlette.requests import Request


class PathGroup(str, Enum):  # noqa: UP042
    """路径组分类 — 决定限流配额档位"""

    READ = "read"  # GET 端点 + POST /api/v1/retrieve
    WRITE = "write"  # POST /api/v1/documents (上传)
    ADMIN = "admin"  # /api/v1/admin/* + /api/v1/diagnostics


# Lua 脚本 — 原子完成 incr + 设置 60s 过期 + 返回当前计数
# 返回: [current_count, ttl_seconds]
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local window = tonumber(ARGV[1])
local current = redis.call('INCR', key)
if current == 1 then
    redis.call('EXPIRE', key, window)
end
local ttl = redis.call('TTL', key)
return {current, ttl}
"""


def classify_path(method: str, path: str) -> PathGroup:
    """根据 HTTP method + 路径前缀归类到 read/write/admin

    规则:
    - /api/v1/admin/* + /api/v1/diagnostics → ADMIN
    - POST /api/v1/documents → WRITE
    - 其他 (GET *, POST /retrieve 等) → READ
    """
    normalized = path.rstrip("/") or "/"
    if normalized.startswith("/api/v1/admin/") or normalized == "/api/v1/diagnostics":
        return PathGroup.ADMIN
    if method == "POST" and normalized == "/api/v1/documents":
        return PathGroup.WRITE
    return PathGroup.READ


def get_tier_limit(tier: str, group: PathGroup, settings: Any) -> int:
    """从 tier_quotas 查 limit

    缺 tier 或 group 时 fallback 到 rate_limit_per_minute (旧单一配额).
    """
    quotas = settings.security.tier_quotas
    if tier in quotas and group.value in quotas[tier]:
        return quotas[tier][group.value]
    return settings.security.rate_limit_per_minute


def get_limit_key(
    *,
    tier: str,
    actor_id: str,
    group: PathGroup,
    prefix: str,
) -> str:
    """构造限流 Redis key

    维度: tier + actor_id + path_group
    不含 path 路径参数, 避免每 doc_id 独立桶 (原实现的严重 bug)
    """
    # actor_id 可能含特殊字符, hash 一下保证 key 合法
    actor_hash = hashlib.sha1(actor_id.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{tier}:{actor_hash}:{group.value}"


def get_ip_fallback_key(*, client_ip: str, group: PathGroup, prefix: str) -> str:
    """未认证请求走 IP 限流的 key (兜底)"""
    return f"{prefix}:anon:{client_ip}:{group.value}"


@dataclass
class RateLimitContext:
    """从 Request 抽取的限流上下文"""

    tier: str
    actor_id: str
    group: PathGroup
    is_authenticated: bool


def extract_context(request: Request, settings: Any) -> RateLimitContext:
    """从 Request 抽取限流上下文

    优先: best-effort 解析 JWT, 取 tier + sub (actor_id)
    兜底: 客户端 IP + 'normal' tier (限流 anon 路径)
    """
    path = request.url.path
    group = classify_path(request.method, path)

    # Best-effort JWT 解析 (不校验签名 — 让 verify_principal 负责)
    auth_header = request.headers.get("authorization", "")
    token = ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()

    if token and token.count(".") == 2:
        try:
            # 不验证签名, 仅解析 claim 用于限流分层
            # 真正的鉴权在 verify_principal (Depends) 里完成
            payload = jwt.decode(token, options={"verify_signature": False})
            tier = str(payload.get("tier") or "normal")
            actor_id = str(payload.get("sub") or "")
            if actor_id:
                return RateLimitContext(
                    tier=tier,
                    actor_id=actor_id,
                    group=group,
                    is_authenticated=True,
                )
        except Exception:
            pass

    # 兜底: IP
    client_ip = request.client.host if request.client else "unknown"
    return RateLimitContext(
        tier="anon",
        actor_id=client_ip,
        group=group,
        is_authenticated=False,
    )


async def check_rate_limit(
    redis: Any,
    *,
    key: str,
    limit: int,
    window: int,
) -> tuple[bool, int, int]:
    """执行限流检查

    Args:
        redis: aioredis 客户端
        key: 限流 key
        limit: 配额上限
        window: 窗口秒数

    Returns:
        (allowed, current_count, retry_after_seconds)
    """
    result = await redis.eval(_SLIDING_WINDOW_LUA, 1, key, window)
    current = int(result[0])
    ttl = int(result[1])
    # ttl=-1 表示 key 无过期 (Lua 极端情况), 退化为 window
    retry_after = ttl if ttl > 0 else window
    return (current <= limit, current, retry_after)
