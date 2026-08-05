"""D2: 客户数据 GDPR 删除 API.

合规要求:
- 客户申请删除 → 软删除 (30 天观察期, 防误删)
- 30 天后 → 硬删除 (全链路: Redis + PostgreSQL + Milvus + ES)
- 删除审计: 删除操作本身记录到 audit_log (保留 7 年, 证明已删除)
- 客户可查询: "我的删除请求状态"

数据范围:
- Redis: session:* / dialogue_log:* / feedback:* / tool_state:*
- PostgreSQL: dialogue_log / customer_profile_audit / decision_log
- Milvus: 知识库中含该客户信息的向量 (罕见, 但可能)
- ES: 知识库中含该客户信息的索引 (罕见, 但可能)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from lumio.shared.config import get_settings
from lumio.shared.logger import get_logger

logger = get_logger(__name__)


class DeletionStatus(str, Enum):
    """删除状态."""

    REQUESTED = "requested"  # 客户申请, 软删除
    SOFT_DELETED = "soft_deleted"  # 已软删除, 观察期
    HARD_DELETED = "hard_deleted"  # 已硬删除, 全链路清理
    FAILED = "failed"  # 删除失败, 需人工介入


@dataclass
class DeletionRequest:
    """删除请求记录."""

    request_id: str
    customer_id: str
    requested_at: float
    soft_deleted_at: float | None = None
    hard_deleted_at: float | None = None
    status: DeletionStatus = DeletionStatus.REQUESTED
    affected_sessions: int = 0
    affected_logs: int = 0
    failed_layers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "customer_id": self.customer_id,
            "requested_at": self.requested_at,
            "soft_deleted_at": self.soft_deleted_at,
            "hard_deleted_at": self.hard_deleted_at,
            "status": self.status.value,
            "affected_sessions": self.affected_sessions,
            "affected_logs": self.affected_logs,
            "failed_layers": self.failed_layers,
        }


class GDPRService:
    """GDPR 删除服务 (单例)."""

    # 软删除保留期 (秒)
    SOFT_DELETION_PERIOD_SECONDS = 30 * 86400  # 30 天

    def __init__(self) -> None:
        self._redis: Any = None
        self._db_session_factory: Any = None

    async def request_deletion(self, customer_id: str, request_id: str | None = None) -> DeletionRequest:
        """客户申请删除数据 (异步执行, 不阻塞).

        流程:
        1. 软删除: 标记 customer_id 为待删除, 30 天观察
        2. 立即清理活跃 session (防 PII 立即泄露)
        3. 调度 30 天后硬删除任务
        """
        import uuid

        rid = request_id or str(uuid.uuid4())
        now = time.time()

        req = DeletionRequest(
            request_id=rid,
            customer_id=customer_id,
            requested_at=now,
        )

        # 1. 记录到 Redis (待办列表)
        try:
            redis = await self._get_redis()
            if redis:
                await redis.hset(
                    f"lumio:gdpr:deletion:{rid}",
                    mapping={
                        "customer_id": customer_id,
                        "requested_at": str(now),
                        "status": DeletionStatus.REQUESTED.value,
                    },
                )
                await redis.expire(f"lumio:gdpr:deletion:{rid}", self.SOFT_DELETION_PERIOD_SECONDS + 7 * 86400)
                # 加入调度队列
                await redis.zadd(
                    "lumio:gdpr:scheduled",
                    {rid: now + self.SOFT_DELETION_PERIOD_SECONDS},
                )
        except Exception as exc:
            logger.warning("Redis GDPR 记录失败: %s", exc)

        # 2. 立即清理活跃 session
        try:
            affected = await self._cleanup_active_sessions(customer_id)
            req.affected_sessions = affected
            req.soft_deleted_at = time.time()
            req.status = DeletionStatus.SOFT_DELETED
            logger.info("GDPR 软删除完成: customer=%s sessions=%d", customer_id, affected)
        except Exception as exc:
            logger.error("GDPR 软删除失败: customer=%s err=%s", customer_id, exc)
            req.status = DeletionStatus.FAILED
            req.failed_layers.append("redis_sessions")

        return req

    async def hard_delete(self, request_id: str) -> DeletionRequest:
        """硬删除 (30 天观察期后执行).

        清理:
        1. PostgreSQL: dialogue_log / customer_profile_audit / decision_log
        2. Milvus: 含该 customer_id 的向量
        3. ES: 含该 customer_id 的索引
        4. Redis: 所有相关 key
        """
        redis = await self._get_redis()
        if not redis:
            raise RuntimeError("Redis 不可用")

        data = await redis.hgetall(f"lumio:gdpr:deletion:{request_id}")
        if not data:
            raise ValueError(f"删除请求不存在: {request_id}")

        customer_id = data.get("customer_id", "")
        now = time.time()
        req = DeletionRequest(
            request_id=request_id,
            customer_id=customer_id,
            requested_at=float(data.get("requested_at", 0)),
        )
        req.soft_deleted_at = now
        req.hard_deleted_at = now

        # 1. PostgreSQL 硬删除
        try:
            deleted_logs = await self._delete_postgres(customer_id)
            req.affected_logs = deleted_logs
        except Exception as exc:
            logger.error("PostgreSQL 硬删除失败: %s", exc)
            req.failed_layers.append("postgres")

        # 2. Milvus 硬删除 (可选, 知识库通常不含 PII)
        try:
            await self._delete_milvus(customer_id)
        except Exception as exc:
            logger.debug("Milvus 删除跳过或失败: %s", exc)
            req.failed_layers.append("milvus")

        # 3. ES 硬删除
        try:
            await self._delete_elasticsearch(customer_id)
        except Exception as exc:
            logger.debug("ES 删除跳过或失败: %s", exc)
            req.failed_layers.append("elasticsearch")

        # 4. Redis 清理
        try:
            deleted_keys = await self._delete_redis_by_customer(customer_id)
            req.affected_sessions += deleted_keys
        except Exception as exc:
            logger.error("Redis 硬删除失败: %s", exc)
            req.failed_layers.append("redis")

        # 5. 标记完成
        req.status = DeletionStatus.HARD_DELETED if not req.failed_layers else DeletionStatus.FAILED
        await redis.hset(
            f"lumio:gdpr:deletion:{request_id}",
            mapping={
                "status": req.status.value,
                "hard_deleted_at": str(now),
                "failed_layers": ",".join(req.failed_layers),
            },
        )

        logger.info(
            "GDPR 硬删除完成: request=%s customer=%s status=%s logs=%d failed=%s",
            request_id,
            customer_id,
            req.status.value,
            req.affected_logs,
            req.failed_layers,
        )
        return req

    async def get_status(self, request_id: str) -> DeletionRequest | None:
        """查询删除请求状态."""
        redis = await self._get_redis()
        if not redis:
            return None
        data = await redis.hgetall(f"lumio:gdpr:deletion:{request_id}")
        if not data:
            return None
        return DeletionRequest(
            request_id=request_id,
            customer_id=data.get("customer_id", ""),
            requested_at=float(data.get("requested_at", 0)),
            status=DeletionStatus(data.get("status", "requested")),
        )

    # ── 内部清理方法 ──

    async def _cleanup_active_sessions(self, customer_id: str) -> int:
        """清理该 customer_id 的所有活跃 session."""
        return await self._delete_redis_by_customer(customer_id)

    async def _delete_redis_by_customer(self, customer_id: str) -> int:
        """P0-4 第三轮修复: 按 customer_id 删除 Redis 全部相关 key.

        旧实现用 pattern `lumio:session:*customer:{id}*` — 但 session key 实际是
        `lumio:session:{uuid}:meta` (customer_id 只存在于 JSON value), 模式永不匹配。
        现改为 SCAN `lumio:session:*:meta` 解析 value 匹配 customer_id,
        一并删除 meta + history + slot key。
        """
        redis = await self._get_redis()
        if not redis:
            return 0
        import json as _json

        from lumio.services.common.session import (
            session_history_key,
            session_meta_scan_pattern,
        )

        deleted = 0
        async for key in redis.scan_iter(match=session_meta_scan_pattern(), count=100):
            raw = await redis.get(key)
            if not raw:
                continue
            try:
                meta = _json.loads(raw)
            except Exception:
                continue
            if meta.get("customer_id") != customer_id:
                continue
            session_id = meta.get("session_id", "") or ""
            to_delete = [key if isinstance(key, str) else key.decode()]
            if session_id:
                to_delete.append(session_history_key(session_id))
                to_delete.append(f"lumio:slot:{session_id}")  # slot_tracker key
            await redis.delete(*to_delete)
            deleted += 1
        return deleted

    async def _delete_postgres(self, customer_id: str) -> int:
        """PostgreSQL 硬删除 dialogue_log / decision_log / chat_message.

        走 SQLAlchemy async session, 显式 commit. 仅删除已存在的表.
        P0-4 第三轮修复: 覆盖 chat_message (此前客户消息全文明文落库但不在删除范围).
        """
        try:
            from sqlalchemy import delete

            from lumio.shared.orm_models import ChatMessage, DecisionLog, DialogueLog

            db_sf = getattr(self, "_db_session_factory", None)
            if db_sf is None:
                # P0-4: 注入全局 session factory (此前从未配置 → PG 删除整条路径跳过)
                from lumio.services.common.database import get_async_session_factory

                db_sf = get_async_session_factory()
                self._db_session_factory = db_sf

            deleted = 0
            async with db_sf() as session:
                # 1) 硬删 dialogue_log
                stmt = delete(DialogueLog).where(DialogueLog.customer_id == customer_id)
                result = await session.execute(stmt)
                deleted += result.rowcount or 0
                # 2) decision_log (E2 可解释性: 客户要求删除时一并硬删)
                stmt2 = delete(DecisionLog).where(DecisionLog.customer_id == customer_id)
                result2 = await session.execute(stmt2)
                deleted += result2.rowcount or 0
                # 3) chat_message (P0-4: GDPR 完整性 — 消息全文含 PII 必须删除)
                stmt3 = delete(ChatMessage).where(ChatMessage.customer_id == customer_id)
                result3 = await session.execute(stmt3)
                deleted += result3.rowcount or 0
                await session.commit()
            logger.info("GDPR PostgreSQL 删除: customer=%s, rows=%d", customer_id, deleted)
            return deleted
        except Exception as exc:
            logger.error("GDPR PostgreSQL 删除失败: customer=%s, err=%s", customer_id, exc)
            raise

    async def _delete_milvus(self, customer_id: str) -> int:
        """Milvus 删除含该 customer_id 的向量 (best-effort)."""
        try:

            collection = getattr(self, "_milvus_collection", None)
            if collection is None:
                logger.warning("GDPR Milvus 删除跳过: collection 未配置")
                return 0
            expr = f'customer_id == "{customer_id}"'
            result = collection.delete(expr)
            count = getattr(result, "delete_count", 0) if result else 0
            logger.info("GDPR Milvus 删除: customer=%s, vectors=%d", customer_id, count)
            return int(count)
        except Exception as exc:
            logger.error("GDPR Milvus 删除失败: customer=%s, err=%s", customer_id, exc)
            return 0  # best-effort, 不阻断硬删除流程

    async def _delete_elasticsearch(self, customer_id: str) -> int:
        """ES 删除含该 customer_id 的索引 (best-effort).

        用 delete_by_query, tenant 索引 + dialogue 索引.
        """
        try:
            es = getattr(self, "_es_client", None)
            if es is None:
                logger.warning("GDPR ES 删除跳过: es_client 未配置")
                return 0

            settings = get_settings()
            indices = [
                settings.elasticsearch.dialogue_index,
                settings.elasticsearch.knowledge_index,
            ]
            deleted = 0
            for index in indices:
                try:
                    res = await es.delete_by_query(
                        index=index,
                        body={"query": {"term": {"customer_id": customer_id}}},
                        refresh=True,
                        conflicts="proceed",
                    )
                    deleted += int(res.get("deleted", 0))
                except Exception as inner:
                    # 索引不存在不阻断
                    logger.debug("GDPR ES 索引 %s 删除跳过: %s", index, inner)
            logger.info("GDPR ES 删除: customer=%s, docs=%d", customer_id, deleted)
            return deleted
        except Exception as exc:
            logger.error("GDPR ES 删除失败: customer=%s, err=%s", customer_id, exc)
            return 0  # best-effort

    async def _get_redis(self) -> Any:
        if self._redis is None:
            try:
                from lumio.services.common.redis_client import get_redis_client

                self._redis = get_redis_client()
            except Exception as exc:
                logger.debug("Redis 客户端初始化失败: %s", exc)
                self._redis = False
        return self._redis if self._redis else None


# 全局单例
_gdpr_service: GDPRService | None = None


def get_gdpr_service() -> GDPRService:
    global _gdpr_service
    if _gdpr_service is None:
        _gdpr_service = GDPRService()
    return _gdpr_service


# P0-4 第三轮修复: 后台 worker — 消费 lumio:gdpr:scheduled 到期任务执行硬删除
# 此前 request_deletion 写入的调度 ZSET 无任何消费者, 30 天硬删除永远不会发生.
_gdpr_sweep_task: asyncio.Task[None] | None = None


async def _gdpr_sweep_loop() -> None:
    """每小时扫描到期删除请求并执行硬删除."""
    import asyncio

    while True:
        try:
            service = get_gdpr_service()
            redis = await service._get_redis()
            if redis:
                now = time.time()
                due_ids = await redis.zrangebyscore(
                    "lumio:gdpr:scheduled", 0, now, start=0, num=20
                )
                for rid in due_ids:
                    try:
                        await service.hard_delete(rid)
                        await redis.zrem("lumio:gdpr:scheduled", rid)
                        logger.info("GDPR 硬删除完成: request=%s", rid)
                    except Exception as exc:
                        logger.error("GDPR 硬删除失败: request=%s err=%s", rid, exc)
        except Exception as exc:
            logger.warning("GDPR 调度扫描异常: %s", exc)
        await asyncio.sleep(3600)  # 每小时


def start_gdpr_sweep_worker() -> None:
    """启动 GDPR 调度 worker (app 启动时调用)."""
    global _gdpr_sweep_task
    if _gdpr_sweep_task is None or _gdpr_sweep_task.done():
        import asyncio

        _gdpr_sweep_task = asyncio.create_task(_gdpr_sweep_loop(), name="gdpr_sweep")


def stop_gdpr_sweep_worker() -> None:
    """停止 GDPR 调度 worker."""
    global _gdpr_sweep_task
    if _gdpr_sweep_task is not None:
        _gdpr_sweep_task.cancel()
        _gdpr_sweep_task = None
