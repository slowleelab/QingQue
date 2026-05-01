"""初始化 Milvus Collection

创建 smartcs_knowledge Collection (v2 — 含过滤字段):
- 向量维度: 1024 (bge-large-zh-v1.5)
- 索引类型: IVF_FLAT (nlist=128)
- 度量类型: COSINE
- 过滤字段: keywords, card_type, customer_tier, effective_date, expiry_date

使用方式:
    poetry run python scripts/init_milvus.py
"""

import sys


def init_milvus():
    from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections

    print("🔧 连接 Milvus...")
    try:
        connections.connect(host="localhost", port="19530")
    except Exception as e:
        print(f"❌ 连接 Milvus 失败: {e}")
        print("   请确保 Milvus 已启动: docker-compose up -d milvus")
        sys.exit(1)

    collection_name = "smartcs_knowledge"

    # 检查是否已存在
    from pymilvus import utility
    if utility.has_collection(collection_name):
        print(f"⚠️  Collection '{collection_name}' 已存在，跳过创建")
        existing = Collection(collection_name)
        print(f"   向量数量: {existing.num_entities}")
        return

    print(f"🔧 创建 Collection '{collection_name}'...")

    # 定义字段（v2: 含过滤字段）
    fields = [
        FieldSchema(
            name="chunk_id",
            dtype=DataType.VARCHAR,
            max_length=64,
            is_primary=True,
            description="知识块唯一标识",
        ),
        FieldSchema(
            name="doc_id",
            dtype=DataType.VARCHAR,
            max_length=64,
            description="源文档 ID",
        ),
        FieldSchema(
            name="content",
            dtype=DataType.VARCHAR,
            max_length=65535,
            description="知识块内容",
        ),
        FieldSchema(
            name="embedding",
            dtype=DataType.FLOAT_VECTOR,
            dim=1024,  # bge-large-zh-v1.5
            description="文本向量 (1024 维)",
        ),
        FieldSchema(
            name="category",
            dtype=DataType.VARCHAR,
            max_length=32,
            description="业务分类",
        ),
        FieldSchema(
            name="doc_type",
            dtype=DataType.VARCHAR,
            max_length=32,
            description="文档类型: faq/rule/rate/activity/product",
        ),
        FieldSchema(
            name="keywords",
            dtype=DataType.VARCHAR,
            max_length=512,
            description="关键词列表（逗号分隔）",
        ),
        FieldSchema(
            name="card_type",
            dtype=DataType.VARCHAR,
            max_length=32,
            description="卡种: 普卡/金卡/白金卡/钻石卡",
        ),
        FieldSchema(
            name="customer_tier",
            dtype=DataType.VARCHAR,
            max_length=32,
            description="客户等级: 普通/银卡/金卡/白金",
        ),
        FieldSchema(
            name="effective_date",
            dtype=DataType.INT64,
            description="生效日期（epoch 毫秒）",
        ),
        FieldSchema(
            name="expiry_date",
            dtype=DataType.INT64,
            description="失效日期（epoch 毫秒）",
        ),
    ]

    # 创建 Schema
    schema = CollectionSchema(
        fields=fields,
        description="智能客服知识库向量索引",
    )

    # Parent-Child 分块字段
    schema.add_field("chunk_type", DataType.VARCHAR, max_length=16, description="分块结构类型")
    schema.add_field("parent_chunk_id", DataType.VARCHAR, max_length=64, description="父块ID")

    # 创建 Collection
    collection = Collection(
        name=collection_name,
        schema=schema,
    )

    # 创建向量索引
    print("🔧 创建 IVF_FLAT 索引...")
    index_params = {
        "metric_type": "COSINE",
        "index_type": "IVF_FLAT",
        "params": {"nlist": 128},
    }
    collection.create_index(
        field_name="embedding",
        index_params=index_params,
    )

    # 加载到内存
    collection.load()

    print(f"✅ Milvus Collection '{collection_name}' 创建成功!")
    print(f"   向量维度: 1024")
    print(f"   索引类型: IVF_FLAT (nlist=128)")
    print(f"   度量类型: COSINE")


if __name__ == "__main__":
    init_milvus()
