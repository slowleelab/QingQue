# Knowledge Platform

银行知识数据平台 — 从 SmartCS 抽离的独立微服务。

## 架构

```
PostgreSQL(真相源) + Elasticsearch(BM25+IK ‖ kNN 原生 RRF) + Reranker
Kafka(异步ETL) + MinIO(原始文档) + Redis(检索缓存)
```

**砍掉 Milvus**：向量进 ES 用 dense_vector + HNSW，原生 RRF retriever 服务端融合，消除双写和手写 RRF。

## 数据流

```
上传 → MinIO → PG(KbDocument) → Kafka(kp.ingest.request)
                                        ↓
                              Worker 消费 → ETL 7阶段
   ┌────────────────────────────────────────────────────┘
   Parse(docling) → Clean → EXTRACT(LLM) → Chunk → Embed → PG(真相源) → ES(派生索引)
                                                                        ↓
                                                              Kafka(kp.ingest.result)

检索: query → embed → ES 原生 RRF(BM25‖kNN) → Reranker → 合规过滤 → 缓存
```

## ETL 7 阶段

| 阶段 | 模块 | 说明 |
|------|------|------|
| Parse | `pipeline/parser.py` | docling 版式感知解析，保留表格语义 |
| Clean | `pipeline/cleaner.py` | 页眉页脚/控制字符/去重 |
| **Extract** | `pipeline/extractor.py` | **LLM 自动抽取关键词/摘要/实体/FAQ** |
| Chunk | `pipeline/chunker.py` | 结构感知分块 (FAQ/层级Parent-Child/表格保护) |
| Embed | `pipeline/embedder.py` | TEI BGE-M3 + 熔断器 |
| PG Write | `pipeline/writer.py` | 真相源：chunk正文+embedding+model_version |
| ES Write | `pipeline/writer.py` | 派生索引：BM25文本+dense_vector |

## 快速开始

```bash
# 1. 启动基础设施
docker-compose up -d

# 2. 安装 ES IK 分词器 (版本须匹配)
docker exec -it knowledge-platform-elasticsearch-1 \
  elasticsearch-plugin install analysis-ik:8.19.9

# 3. 安装依赖
poetry install

# 4. 初始化数据库 + ES 索引
python scripts/init_database.py
python scripts/init_elasticsearch.py

# 5. 启动 Worker (消费 Kafka ETL 任务)
poetry run kp-worker

# 6. 启动 API 服务
poetry run kp-api

# 7. 导入种子数据
python scripts/seed_knowledge.py --dir ../agent/test_data

# 8. 运行 RAGAS 评估
python -m app.eval.ragas_eval
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/documents` | 上传文档 → Kafka 异步 ETL |
| GET | `/api/v1/documents/{id}` | 查询文档状态 |
| GET | `/api/v1/documents` | 文档列表 |
| POST | `/api/v1/documents/{id}/reindex` | 重建 ES 索引 |
| POST | `/api/v1/retrieve` | 混合检索 (RRF + Reranker) |
| GET | `/health` | 健康检查 |

## 关键设计决策

1. **砍 Milvus**：50万向量级 ES 单库舒适区，消除双写一致性
2. **ES 原生 RRF**：服务端融合 BM25+IK 与 kNN，消除 Python 手写 RRF
3. **docling 版式感知**：保留银行表格语义，提升召回
4. **LLM 自动抽取**：替代人工 YAML frontmatter
5. **Kafka 异步**：大文件不阻塞 API
6. **PG 留 embedding**：ES 重建不需重跑模型
7. **model_version 治理**：影子索引灰度切换
8. **RAGAS 评估**：检索质量回归门禁

## 从 SmartCS 迁移的变更

| SmartCS | Knowledge Platform |
|---------|-------------------|
| ES + Milvus 双写 | ES 单写（PG 真相源） |
| Python 手写 RRF | ES 原生 RRF retriever |
| pymupdf 纯文本 | docling 版式感知 |
| 人工 YAML 抽取 | LLM 自动抽取 |
| 同步阻塞 API | Kafka 异步 Worker |
| 无质量基线 | RAGAS golden query 评估 |
| 无嵌入版本治理 | model_version + 影子索引 |
