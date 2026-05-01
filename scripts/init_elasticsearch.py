"""初始化 Elasticsearch 索引

创建 smartcs_knowledge 索引:
- IK 分词器 (ik_max_word 索引, ik_smart 搜索)
- BM25 检索字段
- 元数据过滤字段

使用方式:
    poetry run python scripts/init_elasticsearch.py
"""

import sys


def init_elasticsearch():
    from elasticsearch import Elasticsearch

    print("🔧 连接 Elasticsearch...")
    try:
        es = Elasticsearch(
            ["http://localhost:9200"],
        )
        if not es.ping():
            raise ConnectionError("ES ping 失败")
    except Exception as e:
        print(f"❌ 连接 Elasticsearch 失败: {e}")
        print("   请确保 ES 已启动: docker-compose up -d elasticsearch")
        sys.exit(1)

    info = es.info()
    print(f"✅ 连接成功: ES {info['version']['number']}")

    # 验证 IK 分词器
    print("\n🔧 验证 IK 分词器...")
    try:
        result = es.indices.analyze(
            body={
                "analyzer": "ik_max_word",
                "text": "信用卡年费减免条件",
            },
        )
        tokens = [t["token"] for t in result["tokens"]]
        print(f"   ik_max_word 分词结果: {tokens}")
    except Exception as e:
        print(f"⚠️  IK 分词器未安装: {e}")
        print("   安装方法: docker exec -it smartcs-elasticsearch elasticsearch-plugin install https://get.infini.cloud/elasticsearch/analysis-ik/8.19.9")
        print("   安装后需重启 ES 容器: docker-compose restart elasticsearch")

    # 创建索引
    index_name = "smartcs_knowledge"

    if es.indices.exists(index=index_name):
        print(f"\n⚠️  索引 '{index_name}' 已存在，跳过创建")
    else:
        print(f"\n🔧 创建索引 '{index_name}'...")

        mapping = {
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
                "analysis": {
                    "analyzer": {
                        "ik_max_word": {
                            "type": "custom",
                            "tokenizer": "ik_max_word",
                        },
                        "ik_smart": {
                            "type": "custom",
                            "tokenizer": "ik_smart",
                        },
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
                    "category": {"type": "keyword"},
                    "doc_type": {"type": "keyword"},
                    "keywords": {"type": "keyword"},
                    "card_type": {"type": "keyword"},
                    "customer_tier": {"type": "keyword"},
                    "effective_date": {"type": "date", "format": "yyyy-MM-dd"},
                    "expiry_date": {"type": "date", "format": "yyyy-MM-dd"},
                    "security_level": {"type": "keyword"},
                    "version": {"type": "keyword"},
                    "chunk_type": {"type": "keyword"},
                    "parent_chunk_id": {"type": "keyword"},
                    "heading_path": {"type": "keyword"},
                    "created_at": {"type": "date"},
                },
            },
        }

        es.indices.create(index=index_name, body=mapping)
        print(f"✅ 索引 '{index_name}' 创建成功!")

    # 测试写入和检索
    print("\n🔧 测试写入和检索...")
    test_doc = {
        "chunk_id": "test_001",
        "doc_id": "TEST_DOC",
        "content": "信用卡年费减免条件：金卡客户年度消费满6次可减免次年年费",
        "category": "年费",
        "doc_type": "faq",
        "keywords": ["年费", "减免", "金卡"],
    }
    es.index(index=index_name, id="test_001", body=test_doc, refresh=True)

    # BM25 检索测试
    search_result = es.search(
        index=index_name,
        body={
            "query": {
                "match": {
                    "content": {
                        "query": "年费减免",
                        "analyzer": "ik_smart",
                    },
                },
            },
        },
    )
    hits = search_result["hits"]["hits"]
    if hits:
        print(f"✅ BM25 检索验证通过，命中 {len(hits)} 条结果")
        print(f"   检索内容: {hits[0]['_source']['content']}")
    else:
        print("❌ BM25 检索验证失败")

    # 清理测试数据
    es.delete(index=index_name, id="test_001", refresh=True)
    print("   测试数据已清理")


if __name__ == "__main__":
    init_elasticsearch()
