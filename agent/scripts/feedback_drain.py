"""D4: 客户赞踩 → Kafka → S3 训练数据飞轮.

问题: 当前 bot/router.py:1403-1415 用 Redis key + 24h TTL, 24h 后赞踩数据消失,
       训练数据完全浪费.

修复:
1. 改写 Redis Stream (替代 24h TTL key)
2. cron 每分钟 XREADGROUP → Kafka topic lumio.feedback.raw
3. Kafka Connect → S3 → 训练数据湖

附加:
- 坐席标 bad case → 候选池 → 人工标注 → 扩 Golden Set
- 周报: 差评 TOP 10 原因分析 → prompt 调优
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from lumio.shared.logger import get_logger
from lumio.shared.metrics import BAD_CASE_MARKED

logger = get_logger(__name__)


FEEDBACK_STREAM_KEY = "lumio:feedback:queue"
FEEDBACK_CONSUMER_GROUP = "lumio-feedback-drain"
FEEDBACK_CONSUMER_NAME = "drain-1"
KAFKA_TOPIC = "lumio.feedback.raw"


@dataclass
class FeedbackRecord:
    """单条反馈记录."""

    feedback_id: str
    session_id: str
    message_id: str
    customer_id_hash: str  # 脱敏
    rating: int  # -1=踩, 1=赞
    reason: str
    bot_reply: str
    user_input: str
    intent: str
    created_at: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_kafka_payload(self) -> bytes:
        return json.dumps(self.__dict__, ensure_ascii=False).encode("utf-8")


class FeedbackDrainer:
    """从 Redis Stream 拉取反馈, 推送到 Kafka."""

    def __init__(self, kafka_bootstrap: str = "localhost:9092") -> None:
        self.kafka_bootstrap = kafka_bootstrap
        self._redis: Any = None
        self._producer: Any = None
        self._running = False

    async def start(self) -> None:
        """启动 drainer 后台任务."""
        self._running = True
        await self._init_redis()
        await self._init_kafka()
        logger.info("FeedbackDrainer 启动, 监听 %s", FEEDBACK_STREAM_KEY)
        while self._running:
            try:
                count = await self.drain_once()
                if count == 0:
                    await asyncio.sleep(60)  # 空时 60s 后重试
                else:
                    logger.info("drain %d 条反馈到 Kafka", count)
            except Exception as exc:
                logger.error("drainer 异常: %s", exc)
                await asyncio.sleep(60)

    async def stop(self) -> None:
        self._running = False
        if self._producer:
            try:
                await self._producer.stop()
            except Exception:
                pass

    async def drain_once(self, batch_size: int = 100) -> int:
        """单次 drain: 从 Redis Stream 拉取 → 推 Kafka → ACK."""
        redis = await self._get_redis()
        if not redis:
            return 0

        # 1. 创建 consumer group (一次性)
        try:
            await redis.xgroup_create(
                FEEDBACK_STREAM_KEY,
                FEEDBACK_CONSUMER_GROUP,
                id="0",
                mkstream=True,
            )
        except Exception:
            pass  # group 已存在

        # 2. 读取消息
        try:
            messages = await redis.xreadgroup(
                FEEDBACK_CONSUMER_GROUP,
                FEEDBACK_CONSUMER_NAME,
                streams={FEEDBACK_STREAM_KEY: ">"},
                count=batch_size,
                block=5000,
            )
        except Exception as exc:
            logger.debug("XREADGROUP 失败: %s", exc)
            return 0

        if not messages:
            return 0

        # 3. 解析 + 推 Kafka + ACK
        producer = await self._get_kafka_producer()
        ack_ids: list[str] = []
        pushed = 0
        for _stream, entries in messages:
            for msg_id, data in entries:
                try:
                    record = FeedbackRecord(
                        feedback_id=msg_id,
                        session_id=data.get(b"session_id", b"").decode(),
                        message_id=data.get(b"message_id", b"").decode(),
                        customer_id_hash=data.get(b"customer_id_hash", b"").decode(),
                        rating=int(data.get(b"rating", 0)),
                        reason=data.get(b"reason", b"").decode(),
                        bot_reply=data.get(b"bot_reply", b"").decode()[:2000],
                        user_input=data.get(b"user_input", b"").decode()[:500],
                        intent=data.get(b"intent", b"").decode(),
                        created_at=float(data.get(b"created_at", 0)),
                    )
                    if producer:
                        await producer.send_and_wait(
                            KAFKA_TOPIC,
                            record.to_kafka_payload(),
                            key=record.session_id.encode(),
                        )
                    pushed += 1
                    ack_ids.append(msg_id)
                except Exception as exc:
                    logger.warning("反馈消息处理失败: id=%s err=%s", msg_id, exc)

        # 4. ACK
        if ack_ids:
            await redis.xack(FEEDBACK_STREAM_KEY, FEEDBACK_CONSUMER_GROUP, *ack_ids)

        return pushed

    async def _init_redis(self) -> None:
        pass  # lazy init

    async def _init_kafka(self) -> None:
        try:
            from aiokafka import AIOKafkaProducer

            self._producer = AIOKafkaProducer(
                bootstrap_servers=self.kafka_bootstrap,
                value_serializer=lambda v: v,
            )
            await self._producer.start()
            logger.info("Kafka producer 已连接: %s", self.kafka_bootstrap)
        except ImportError:
            logger.warning("aiokafka 未安装, 反馈数据仅入 Redis Stream 不入 Kafka")
        except Exception as exc:
            logger.warning("Kafka 连接失败: %s", exc)

    async def _get_redis(self) -> Any:
        if self._redis is None:
            try:
                from lumio.services.common.redis_client import get_redis_client

                self._redis = get_redis_client()
            except Exception as exc:
                logger.debug("Redis 客户端初始化失败: %s", exc)
                self._redis = False
        return self._redis if self._redis else None

    async def _get_kafka_producer(self) -> Any:
        return self._producer


# ── bad case 标记器 (坐席端) ──

class BadCaseMarker:
    """坐席标记差评案例 → 入 Golden Set 候选池."""

    def __init__(self) -> None:
        self._redis: Any = None

    async def mark_bad_case(
        self,
        session_id: str,
        turn_id: str,
        reason: str,
        bot_reply: str,
        user_input: str,
        expected_reply: str | None = None,
    ) -> str:
        """标记一个差评案例.

        Returns:
            case_id: 候选池中的案例 ID
        """
        import uuid

        case_id = f"BAD-{uuid.uuid4().hex[:8]}"
        BAD_CASE_MARKED.labels(reason=reason).inc()

        redis = await self._get_redis()
        if redis:
            await redis.hset(
                f"lumio:badcase:pool:{case_id}",
                mapping={
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "reason": reason,
                    "bot_reply": bot_reply[:2000],
                    "user_input": user_input[:1000],
                    "expected_reply": expected_reply or "",
                    "created_at": str(time.time()),
                    "status": "pending",  # pending / reviewed / promoted
                },
            )
        logger.info("Bad case 标记: case_id=%s reason=%s", case_id, reason)
        return case_id

    async def promote_to_golden(self, case_id: str) -> bool:
        """人工审核通过, 提升到 Golden Set (人工操作)."""
        redis = await self._get_redis()
        if not redis:
            return False
        data = await redis.hgetall(f"lumio:badcase:pool:{case_id}")
        if not data:
            return False
        # 追加到 golden_set 候选文件 (人工编辑)
        await redis.hset(
            f"lumio:badcase:pool:{case_id}",
            mapping={"status": "promoted", "promoted_at": str(time.time())},
        )
        logger.info("Bad case 提升到 Golden Set: case_id=%s", case_id)
        return True

    async def _get_redis(self) -> Any:
        if self._redis is None:
            try:
                from lumio.services.common.redis_client import get_redis_client

                self._redis = get_redis_client()
            except Exception as exc:
                logger.debug("Redis 客户端初始化失败: %s", exc)
                self._redis = False
        return self._redis if self._redis else None
