"""初始化 Milvus Collection

创建 smartcs_knowledge Collection (v2.1 — 标量索引 + ARRAY keywords):
- 向量维度: 1024 (bge-large-zh-v1.5)
- 索引类型: IVF_FLAT (nlist=128)
- 度量类型: COSINE
- 过滤字段: keywords(ARRAY), category, doc_type, card_type, customer_tier,
            security_level, effective_date, expiry_date
- 标量索引: chunk_type, category, doc_type, security_level, doc_id

使用方式:
    poetry run python scripts/init_milvus.py
"""

import sys


def init_milvus():
    from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility

    print("🔧 连接 Milvus...")
    try:
        connections.connect(host="localhost", port="19530")
    except Exception as e:
        print(f"❌ 连接 Milvus 失败: {e}")
        print("   请确保 Milvus 已启动: docker-compose up -d milvus")
        sys.exit(1)

    collection_name = "smartcs_knowledge"

    # 删除旧 Collection（仅开发环境，数据可重建）
    if utility.has_collection(collection_name):
        print(f"⚠️  删除旧 Collection '{collection_name}'...")
        utility.drop_collection(collection_name)

    print(f"🔧 创建 Collection '{collection_name}'...")

    fields = [
        FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=64,
                    is_primary=True, description="知识块唯一标识"),
        FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=64,
                    description="源文档 ID"),
        FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535,
                    description="知识块内容"),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=1024,
                    description="文本向量 (1024 维)"),
        FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=32,
                    description="业务分类"),
        FieldSchema(name="doc_type", dtype=DataType.VARCHAR, max_length=32,
                    description="文档类型: faq/rule/rate/activity/product"),
        # v2.1: keywords 改为 ARRAY 类型，支持 ARRAY_CONTAINS 精确过滤
        FieldSchema(name="keywords", dtype=DataType.ARRAY, element_type=DataType.VARCHAR,
                    max_capacity=32, max_length=32,
                    description="关键词列表"),
        FieldSchema(name="card_type", dtype=DataType.VARCHAR, max_length=32,
                    description="卡种: 普卡/金卡/白金卡/钻石卡"),
        FieldSchema(name="customer_tier", dtype=DataType.VARCHAR, max_length=32,
                    description="客户等级: 普通/银卡/金卡/白金"),
        FieldSchema(name="security_level", dtype=DataType.VARCHAR, max_length=16,
                    description="安全级别: public/internal/confidential"),
        FieldSchema(name="effective_date", dtype=DataType.INT64,
                    description="生效日期（epoch 秒）"),
        FieldSchema(name="expiry_date", dtype=DataType.INT64,
                    description="失效日期（epoch 秒）"),
        FieldSchema(name="chunk_type", dtype=DataType.VARCHAR, max_length=16,
                    description="分块结构类型: parent/child"),
        FieldSchema(name="parent_chunk_id", dtype=DataType.VARCHAR, max_length=64,
                    description="父块 ID"),
    ]

    schema = CollectionSchema(fields=fields, description="智能客服知识库向量索引 v2.1")
    collection = Collection(name=collection_name, schema=schema)

    # 向量索引
    print("🔧 创建 IVF_FLAT 向量索引...")
    collection.create_index(
        field_name="embedding",
        index_params={"metric_type": "COSINE", "index_type": "IVF_FLAT", "params": {"nlist": 128}},
    )

    # 标量索引（加速过滤查询）
    print("🔧 创建标量索引...")
    scalar_fields = ["chunk_type", "category", "doc_type", "security_level", "doc_id"]
    for field_name in scalar_fields:
        try:
            collection.create_index(
                field_name=field_name,
                index_params={"index_type": "INVERTED"},
            )
            print(f"   ✅ {field_name}")
        except Exception as e:
            print(f"   ⚠️  {field_name} 索引创建失败: {e}")

    collection.load()
    print(f"✅ Collection '{collection_name}' 创建成功 (1024维 COSINE IVF_FLAT + 5 个标量索引)")


if __name__ == "__main__":
    init_milvus()
