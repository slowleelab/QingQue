"""一键验证所有中间件连通性

逐个检查每个中间件的连接状态，输出汇总报告。

使用方式:
    poetry run python scripts/verify_all.py
"""

import os
import sys


def _get_env(key: str, default: str = "") -> str:
    """从环境变量获取配置值"""
    return os.environ.get(key, default)


def check_postgresql() -> tuple[bool, str]:
    """检查 PostgreSQL"""
    try:
        import asyncio

        from sqlalchemy.ext.asyncio import create_async_engine

        user = _get_env("POSTGRES_USER", "smartcs")
        password = _get_env("POSTGRES_PASSWORD", "smartcs_pass")
        host = _get_env("POSTGRES_HOST", "localhost")
        port = _get_env("POSTGRES_PORT", "5432")
        database = _get_env("POSTGRES_DATABASE", "smartcs")

        async def _check():
            engine = create_async_engine(
                f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}",
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

        endpoint = _get_env("MINIO_ENDPOINT", "localhost:9000")
        access_key = _get_env("MINIO_ACCESS_KEY", "minioadmin")
        secret_key = _get_env("MINIO_SECRET_KEY", "minioadmin")
        secure = _get_env("MINIO_SECURE", "false").lower() == "true"

        client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
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
        import json
        import urllib.request
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            return True, f"models: {models}" if models else "无模型"
    except Exception as e:
        return False, str(e)[:80]


def _mcp_enabled() -> bool:
    """MCP 工具层是否启用（决定是否校验 Nacos / Higress 网关）"""
    return _get_env("MCP_ENABLED", "false").lower() == "true"


def check_nacos() -> tuple[bool, str]:
    """检查 Nacos（MCP Registry）。MCP 关闭时跳过（视为通过，保证默认 make verify 全绿）"""
    if not _mcp_enabled():
        return True, "已跳过（MCP_ENABLED=false）"
    try:
        import urllib.request

        addr = _get_env("NACOS_SERVER_ADDR", "localhost:8848")
        url = f"http://{addr}/nacos/v1/console/health/readiness"
        with urllib.request.urlopen(url, timeout=5) as resp:
            ok = resp.status == 200
            return ok, "就绪" if ok else f"HTTP {resp.status}"
    except Exception as e:
        return False, str(e)[:80]


def check_higress() -> tuple[bool, str]:
    """检查 Higress AI 网关。MCP 关闭时跳过（视为通过，保证默认 make verify 全绿）"""
    if not _mcp_enabled():
        return True, "已跳过（MCP_ENABLED=false）"
    try:
        import urllib.error
        import urllib.request

        endpoint = _get_env("MCP_ENDPOINT", "http://localhost:10000/mcp/credit-card")
        try:
            with urllib.request.urlopen(endpoint, timeout=5) as resp:
                return resp.status < 500, f"HTTP {resp.status}"
        except urllib.error.HTTPError as he:
            # 4xx（如未带鉴权头/方法不符）也说明网关在线并可路由
            return he.code < 500, f"HTTP {he.code}（网关在线）"
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
        ("Nacos (MCP Registry)", check_nacos),
        ("Higress AI 网关", check_higress),
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
