"""初始化 MinIO Bucket

创建 smartcs-docs 桶（幂等操作），设置桶策略为 private。

使用方式:
    poetry run python scripts/init_minio.py
"""

import sys


def init_minio():
    from minio import Minio

    print("🔧 连接 MinIO...")
    try:
        client = Minio(
            "localhost:9000",
            access_key="minioadmin",
            secret_key="minioadmin",
            secure=False,
        )
    except Exception as e:
        print(f"❌ 连接 MinIO 失败: {e}")
        print("   请确保 MinIO 已启动: docker-compose up -d minio")
        sys.exit(1)

    bucket_name = "smartcs-docs"

    if client.bucket_exists(bucket_name):
        print(f"⚠️  Bucket '{bucket_name}' 已存在，跳过创建")
    else:
        print(f"🔧 创建 Bucket '{bucket_name}'...")
        client.make_bucket(bucket_name)
        print(f"✅ Bucket '{bucket_name}' 创建成功!")

    print(f"✅ MinIO 初始化完成!")


if __name__ == "__main__":
    init_minio()
