"""一键验证所有中间件连通性

逐个检查每个中间件的连接状态，输出汇总报告。

使用方式:
    poetry run python scripts/verify_all.py
"""

import sys
import time


def check_postgresql() -> tuple[bool, str]:
    """检查 PostgreSQL"""
    try:
        import asyncio
        from sqlalchemy.ext.asyncio import create_async_engine

        async def _check():
            engine = create_async_engine(
                "postgresql+asyncpg://smartcs:smartcs_pass@localhost:5432/smartcs",
            )
            async with engine.connect() as conn:
                result = await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
                await engine.dispose()
                return result.scalar() == 1

        ok = asyncio.run(_check())
        return ok, "连接正常" if ok else "查询失败"
    except Exception as e:
        return False, str(e)[:80]


def check_redis() -> tuple[bool, str]:
    """检查 Redis"""
    try:
        import redis
        r = redis.Redis(host="localhost", port=6379, decode_responses=True)
        r.ping()
        info = r.info("server")
        r.close()
        return True, f"Redis {info['redis_version']}"
    except Exception as e:
        return False, str(e)[:80]


def check_elasticsearch() -> tuple[bool, str]:
    """检查 Elasticsearch"""
    try:
        from elasticsearch import Elasticsearch
        es = Elasticsearch(["http://localhost:9200"])
        info = es.info()
        es.close()
        return True, f"ES {info['version']['number']}"
    except Exception as e:
        return False, str(e)[:80]


def check_milvus() -> tuple[bool, str]:
    """检查 Milvus"""
    try:
        from pymilvus import connections, utility
        connections.connect(host="localhost", port="19530")
        version = utility.get_server_version()
        connections.disconnect("default")
        return True, f"Milvus {version}"
    except Exception as e:
        return False, str(e)[:80]


def check_minio() -> tuple[bool, str]:
    """检查 MinIO"""
    try:
        from minio import Minio
        client = Minio("localhost:9000", access_key="minioadmin", secret_key="minioadmin", secure=False)
        buckets = client.list_buckets()
        return True, f"{len(buckets)} buckets"
    except Exception as e:
        return False, str(e)[:80]


def check_kafka() -> tuple[bool, str]:
    """检查 Kafka"""
    try:
        import asyncio
        from aiokafka import AIOKafkaConsumer

        async def _check():
            consumer = AIOKafkaConsumer(bootstrap_servers="localhost:9094")
            await consumer.start()
            topics = await consumer.topics()
            try:
                await consumer.stop()
            except Exception:
                pass  # aiokafka stop 时可能抛 CancelledError，不影响连通性判断
            return len(topics)

        count = asyncio.run(_check())
        return True, f"{count} topics"
    except Exception as e:
        return False, str(e)[:80]


def check_ollama() -> tuple[bool, str]:
    """检查 Ollama"""
    try:
        import urllib.request
        import json
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            return True, f"models: {models}" if models else "无模型"
    except Exception as e:
        return False, str(e)[:80]


def main():
    checks = [
        ("PostgreSQL 16", check_postgresql),
        ("Redis 7.2", check_redis),
        ("Elasticsearch 8.19", check_elasticsearch),
        ("Milvus 2.4", check_milvus),
        ("MinIO", check_minio),
        ("Kafka 3.7", check_kafka),
        ("Ollama", check_ollama),
    ]

    print("=" * 60)
    print("SmartCS 中间件连通性验证")
    print("=" * 60)

    results = []
    for name, check_fn in checks:
        print(f"🔍 检查 {name}...", end=" ", flush=True)
        try:
            ok, detail = check_fn()
            status = "✅" if ok else "❌"
            print(f"{status} {detail}")
            results.append((name, ok))
        except Exception as e:
            print(f"❌ {str(e)[:60]}")
            results.append((name, False))

    print("=" * 60)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"验证结果: {passed}/{total} 通过")

    if passed < total:
        print("\n⚠️  未通过的中间件:")
        for name, ok in results:
            if not ok:
                print(f"   - {name}")
        sys.exit(1)
    else:
        print("\n🎉 所有中间件连接正常!")


if __name__ == "__main__":
    main()
