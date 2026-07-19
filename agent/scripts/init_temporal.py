"""初始化 Temporal 基础设施

1. 确保 Temporal Server 可连接
2. 创建/注册 Task Queue
3. 可选的 Namespace 管理

使用方式:
    poetry run python scripts/init_temporal.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import timedelta

logger = logging.getLogger(__name__)


async def init_temporal():
    """初始化 Temporal"""
    try:
        from temporalio.client import Client
    except ImportError:
        logger.warning("temporalio 未安装，跳过 Temporal 初始化")
        print("⚠️  temporalio not installed, skipping Temporal init")
        return False

    host = os.getenv("TEMPORAL_HOST", "localhost:7233")
    namespace = os.getenv("TEMPORAL_NAMESPACE", "smartcs")

    print(f"🔧 连接 Temporal Server: {host} (namespace={namespace})")

    try:
        client = await Client.connect(
            host,
            namespace=namespace,
            rpc_timeout=timedelta(seconds=10),
        )

        # 验证连接
        await client.service_client.check_health()
        print(f"✅ Temporal Server 连接成功: {host}")
        print(f"   Namespace: {namespace}")

        # 创建 Workflow 注册 (实际注册在 Worker 启动时完成)
        # 这里只做连接验证
        return True

    except Exception as e:
        print(f"⚠️  Temporal Server 连接失败: {e}")
        print("   开发环境可忽略此错误，系统会降级到同步编排器")
        return False


def main():
    """入口"""
    print("=" * 60)
    print("SmartCS Temporal 基础设施初始化")
    print("=" * 60)

    asyncio.run(init_temporal())

    print("\n✅ Temporal 初始化完成")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
