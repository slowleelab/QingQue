"""初始化 Kafka Topic

创建以下 Topic:
- smartcs.knowledge.update  知识库更新事件
- smartcs.audit.log         审计日志
- smartcs.call.summary      话后小结

使用方式:
    poetry run python scripts/init_kafka.py
"""

import subprocess
import sys


def init_kafka():
    topics = [
        ("smartcs.knowledge.update", "3"),
        ("smartcs.audit.log", "3"),
        ("smartcs.call.summary", "3"),
    ]

    print("🔧 连接 Kafka...")

    # 列出已有 Topic
    try:
        result = subprocess.run(
            [
                "docker", "exec", "smartcs-kafka",
                "/opt/kafka/bin/kafka-topics.sh",
                "--bootstrap-server", "localhost:9092",
                "--list",
            ],
            capture_output=True, text=True, timeout=15,
        )
        existing = set(result.stdout.strip().split("\n")) if result.stdout.strip() else set()
    except Exception as e:
        print(f"❌ 连接 Kafka 失败: {e}")
        print("   请确保 Kafka 已启动: make up")
        sys.exit(1)

    # 创建不存在的 Topic
    created = 0
    for topic_name, partitions in topics:
        if topic_name in existing:
            print(f"   ⚠️  {topic_name} 已存在，跳过")
        else:
            result = subprocess.run(
                [
                    "docker", "exec", "smartcs-kafka",
                    "/opt/kafka/bin/kafka-topics.sh",
                    "--bootstrap-server", "localhost:9092",
                    "--create", "--topic", topic_name,
                    "--partitions", partitions,
                    "--replication-factor", "1",
                ],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                print(f"   ✅ {topic_name} (partitions={partitions})")
                created += 1
            else:
                print(f"   ❌ 创建 {topic_name} 失败: {result.stderr.strip()}")

    # 列出所有 smartcs Topic
    result = subprocess.run(
        [
            "docker", "exec", "smartcs-kafka",
            "/opt/kafka/bin/kafka-topics.sh",
            "--bootstrap-server", "localhost:9092",
            "--list",
        ],
        capture_output=True, text=True, timeout=15,
    )
    smartcs_topics = [t for t in result.stdout.strip().split("\n") if t.startswith("smartcs.")]
    print(f"\n📋 当前 smartcs Topic 列表:")
    for t in smartcs_topics:
        print(f"   - {t}")

    print(f"\n✅ Kafka Topic 初始化完成! (新建 {created} 个)")


if __name__ == "__main__":
    init_kafka()
