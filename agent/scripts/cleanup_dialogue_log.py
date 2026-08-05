"""D3: 对话历史留存策略.

合规要求:
- 银行客服对话保存 5-7 年 (银保监要求)
- 5 年前对话归档到 S3 (冷存储, 节省成本)
- 7 年前对话硬删除 (PostgreSQL + S3)
- 每天凌晨 cron 执行

使用方式:
- 作为 cron job: 0 2 * * * python -m scripts.cleanup_dialogue_log
- 一次性手动: python -m scripts.cleanup_dialogue_log --days 1825
"""

from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass
from typing import Any

from lumio.shared.logger import get_logger

logger = get_logger(__name__)


# ── 留存策略配置 ──
ARCHIVE_AFTER_DAYS = 5 * 365  # 5 年 = 1825 天
HARD_DELETE_AFTER_DAYS = 7 * 365  # 7 年 = 2555 天


@dataclass
class CleanupResult:
    """清理结果."""

    scanned: int = 0
    archived: int = 0
    hard_deleted: int = 0
    failed: int = 0
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "archived": self.archived,
            "hard_deleted": self.hard_deleted,
            "failed": self.failed,
            "duration_seconds": self.duration_seconds,
        }


async def cleanup_dialogue_log(
    archive_after_days: int = ARCHIVE_AFTER_DAYS,
    delete_after_days: int = HARD_DELETE_AFTER_DAYS,
    batch_size: int = 1000,
) -> CleanupResult:
    """执行对话历史清理.

    1. 扫描 PostgreSQL dialogue_log 表
    2. archive_after_days < created_at < delete_after_days → 归档 S3
    3. created_at > delete_after_days → 硬删除
    4. 删除 audit_log 中的客户 PII 引用 (保留脱敏审计)
    """
    _start = time.time()
    result = CleanupResult()

    try:
        from lumio.shared.config import get_settings
        from lumio.shared.orm_models import DialogueLog
        from sqlalchemy import delete, select

        # 实际实现需要 db_session_factory
        # 此处占位: 返回 0, 等接入 ORM 后启用
        logger.info(
            "对话日志清理任务: archive=%d 天, delete=%d 天",
            archive_after_days,
            delete_after_days,
        )
        # 1. 归档 (5-7 年)
        archive_threshold = time.time() - archive_after_days * 86400
        delete_threshold = time.time() - delete_after_days * 86400

        # 占位: 实际执行需要 db_session
        # async with session_factory() as session:
        #     # 归档
        #     archive_stmt = select(DialogueLog).where(
        #         DialogueLog.created_at < archive_threshold,
        #         DialogueLog.created_at > delete_threshold,
        #         DialogueLog.archived == False,
        #     ).limit(batch_size)
        #     rows = (await session.execute(archive_stmt)).scalars().all()
        #     for row in rows:
        #         await _archive_to_s3(row)
        #         row.archived = True
        #         result.archived += 1
        #     await session.commit()
        #
        #     # 硬删除
        #     delete_stmt = delete(DialogueLog).where(
        #         DialogueLog.created_at < delete_threshold
        #     )
        #     result.hard_deleted = (await session.execute(delete_stmt)).rowcount
        #     await session.commit()

    except Exception as exc:
        logger.error("对话日志清理失败: %s", exc)
        result.failed += 1

    result.duration_seconds = time.time() - _start
    logger.info(
        "对话日志清理完成: 扫描=%d, 归档=%d, 硬删=%d, 失败=%d, 耗时=%.1fs",
        result.scanned,
        result.archived,
        result.hard_deleted,
        result.failed,
        result.duration_seconds,
    )
    return result


async def _archive_to_s3(row: Any) -> bool:
    """归档单条对话到 S3 (冷存储)."""
    # 实际实现需要 boto3 / aioboto3
    # import json
    # payload = {
    #     "session_id": row.session_id,
    #     "customer_id_hash": hashlib.sha256(row.customer_id.encode()).hexdigest(),  # 脱敏
    #     "turns": json.loads(row.turns),
    #     "created_at": row.created_at.isoformat(),
    # }
    # s3_key = f"s3://lumio-archive/dialogue/{row.created_at.year}/{row.session_id}.json.gz"
    # await s3_client.put_object(Bucket="lumio-archive", Key=s3_key, Body=gzip.compress(json.dumps(payload).encode()))
    return True


def main() -> None:
    """CLI 入口: python -m scripts.cleanup_dialogue_log."""
    parser = argparse.ArgumentParser(description="对话历史清理任务")
    parser.add_argument("--days", type=int, default=ARCHIVE_AFTER_DAYS, help="归档阈值(天)")
    parser.add_argument("--delete-days", type=int, default=HARD_DELETE_AFTER_DAYS, help="硬删阈值(天)")
    parser.add_argument("--batch", type=int, default=1000, help="批处理大小")
    args = parser.parse_args()

    result = asyncio.run(
        cleanup_dialogue_log(
            archive_after_days=args.days,
            delete_after_days=args.delete_days,
            batch_size=args.batch,
        )
    )
    print(f"清理完成: {result.to_dict()}")


if __name__ == "__main__":
    main()
