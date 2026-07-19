"""Redis 客户端管理 — 检索缓存 + 分布式锁 + 重试计数"""

from __future__ import annotations

import logging
import uuid

import redis.asyncio as aioredis

from app.config import get_settings

logger = logging.getLogger(__name__)

_client: aioredis.Redis | None = None


async def init_redis() -> aioredis.Redis:
    """初始化 Redis 异步客户端"""
    global _client
    settings = get_settings()
    _client = aioredis.from_url(
        settings.redis.url,
        max_connections=settings.redis.max_connections,
        decode_responses=True,
    )
    try:
        await _client.ping()
        logger.info("Redis 连接成功: %s:%d", settings.redis.host, settings.redis.port)
    except Exception as e:
        logger.warning("Redis 连接失败: %s", e)
    return _client


async def close_redis() -> None:
    """关闭 Redis 客户端"""
    global _client
    if _client:
        await _client.aclose()
        _client = None
        logger.info("Redis 客户端已关闭")


def get_redis() -> aioredis.Redis | None:
    """获取 Redis 客户端"""
    return _client


# ── 分布式锁 ──


async def acquire_lock(key: str, ttl: int = 600) -> str | None:
    """获取分布式锁（SET NX EX）

    Args:
        key: 锁键名
        ttl: 锁过期时间（秒），防止持锁进程崩溃后死锁

    Returns:
        锁 token（释放时验证），获取失败返回 None
    """
    redis = get_redis()
    if redis is None:
        return None

    token = str(uuid.uuid4())
    lock_key = f"kp:lock:{key}"
    acquired = await redis.set(lock_key, token, nx=True, ex=ttl)
    if acquired:
        logger.info("分布式锁已获取", key=key)
        return token
    logger.warning("分布式锁获取失败（已被占用）", key=key)
    return None


async def release_lock(key: str, token: str) -> bool:
    """释放分布式锁（验证 token 防止误释放）

    使用 Lua 脚本保证 GET + DEL 原子性。
    """
    redis = get_redis()
    if redis is None:
        return False

    lock_key = f"kp:lock:{key}"
    # Lua 脚本：token 匹配才删除
    lua_script = """
    if redis.call('GET', KEYS[1]) == ARGV[1] then
        return redis.call('DEL', KEYS[1])
    else
        return 0
    end
    """
    result = await redis.eval(lua_script, 1, lock_key, token)
    if result:
        logger.info("分布式锁已释放", key=key)
        return True
    logger.warning("分布式锁释放失败（token 不匹配或已过期）", key=key)
    return False


# ── 重试计数（持久化，Worker 重启不丢失） ──


async def get_retry_count(doc_id: str) -> int:
    """获取消息重试计数"""
    redis = get_redis()
    if redis is None:
        return 0
    count = await redis.get(f"kp:retry:{doc_id}")
    return int(count) if count else 0


async def increment_retry(doc_id: str, ttl: int = 3600) -> int:
    """增加重试计数，返回当前值"""
    redis = get_redis()
    if redis is None:
        return 0
    key = f"kp:retry:{doc_id}"
    count = await redis.incr(key)
    await redis.expire(key, ttl)
    return count


async def clear_retry(doc_id: str) -> None:
    """清除重试计数（成功或投递 DLQ 后）"""
    redis = get_redis()
    if redis is None:
        return
    await redis.delete(f"kp:retry:{doc_id}")
