"""初始化 Elasticsearch 索引

创建 kp_kb_chunks 索引：
- IK 分词器 (ik_max_word 索引, ik_smart 搜索) — 中文 BM25
- dense_vector 字段 (1024 维, HNSW, cosine) — kNN 向量召回
- 原生 RRF retriever 融合 BM25 ‖ kNN

使用方式: python scripts/init_elasticsearch.py
"""

import sys


def init_elasticsearch() -> None:
    from elasticsearch import Elasticsearch

    from app.config import get_settings

    settings = get_settings()
    index_name = settings.elasticsearch.chunks_index

    print("连接 Elasticsearch...")
    try:
        es = Elasticsearch([settings.elasticsearch.hosts])
        if not es.ping():
            raise ConnectionError("ES ping 失败")
    except Exception as e:
        print(f"连接 Elasticsearch 失败: {e}")
        sys.exit(1)

    info = es.info()
    print(f"连接成功: ES {info['version']['number']}")

    # 验证 IK 分词器
    print("\n验证 IK 分词器...")
    try:
        result = es.indices.analyze(
            body={"analyzer": "ik_max_word", "text": "信用卡年费减免条件"},
        )
        tokens = [t["token"] for t in result["tokens"]]
        print(f"  ik_max_word 分词: {tokens}")
    except Exception as e:
        print(f"  IK 分词器未安装: {e}")
        print("  安装: bin/elasticsearch-plugin install analysis-ik (版本须匹配)")

    if es.indices.exists(index=index_name):
        print(f"\n索引 '{index_name}' 已存在，跳过创建")
        return

    print(f"\n创建索引 '{index_name}'...")

    mapping = {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "analysis": {
                "analyzer": {
                    "ik_max_word": {"type": "custom", "tokenizer": "ik_max_word"},
                    "ik_smart": {"type": "custom", "tokenizer": "ik_smart"},
                },
            },
        },
        "mappings": {
            "properties": {
                "chunk_id": {"type": "keyword"},
                "doc_id": {"type": "keyword"},
                "content": {
                    "type": "text",
                    "analyzer": "ik_max_word",
                    "search_analyzer": "ik_smart",
                },
                "embedding": {
                    "type": "dense_vector",
                    "dims": settings.rag.embedding_dim,
                    "index": True,
                    "similarity": "cosine",
                    "index_options": {"type": "hnsw", "m": 16, "ef_construction": 100},
                },
                "model_version": {"type": "keyword"},
                "category": {"type": "keyword"},
                "doc_type": {"type": "keyword"},
                "keywords": {"type": "keyword"},
                "card_type": {"type": "keyword"},
                "customer_tier": {"type": "keyword"},
                "security_level": {"type": "keyword"},
                "version": {"type": "keyword"},
                "chunk_type": {"type": "keyword"},
                "parent_chunk_id": {"type": "keyword"},
                "heading_path": {"type": "keyword"},
                "approval_status": {"type": "keyword"},
                "is_current_version": {"type": "boolean"},
                "doc_group": {"type": "keyword"},
                "effective_date": {"type": "date", "format": "epoch_second"},
                "expiry_date": {"type": "date", "format": "epoch_second"},
                "created_at": {"type": "date"},
            },
        },
    }

    es.indices.create(index=index_name, body=mapping)
    print(f"索引 '{index_name}' 创建成功!")

    # 验证 RRF retriever (ES 8.14+)
    print("\n验证 RRF retriever 可用性...")
    try:
        es.search(
            index=index_name,
            body={
                "retriever": {
                    "rrf": {
                        "retrievers": [
                            {"standard": {"query": {"match_all": {}}}},
                        ],
                        "rank_window_size": 10,
                    },
                },
                "size": 0,
            },
        )
        print("  RRF retriever 可用 (ES 8.14+)")
    except Exception as e:
        print(f"  RRF retriever 不可用，需 ES 8.14+: {e}")

    print("\n初始化完成!")


if __name__ == "__main__":
    init_elasticsearch()
