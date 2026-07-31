# kb-service（灵智知识检索服务）

灵智知识检索服务 — 从 Lumio 抽离的独立微服务（ETL 管线 + 混合检索 API）。

## 架构

```
PostgreSQL(真相源) + Elasticsearch(BM25+IK ‖ kNN 原生 RRF) + Reranker
Kafka(异步ETL) + MinIO(原始文档) + Redis(检索缓存)
```

**砍掉 Milvus**：向量进 ES 用 dense_vector + HNSW，原生 RRF retriever 服务端融合，消除双写和手写 RRF。

## 数据流

```
上传 → MinIO → PG(KbDocument) → Kafka(kb.ingest.request)
                                        ↓
                              Worker 消费 → ETL 7阶段
   ┌────────────────────────────────────────────────────┘
   Parse(docling) → Clean → EXTRACT(LLM) → Chunk → Embed → PG(真相源) → ES(派生索引)
                                                                        ↓
                                                              Kafka(kb.ingest.result)

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
docker exec -it kb-service-elasticsearch-1 \
  elasticsearch-plugin install analysis-ik:8.19.9

# 3. 安装依赖
poetry install

# 4. 初始化数据库 + ES 索引
python scripts/init_database.py
python scripts/init_elasticsearch.py

# 5. 启动 Worker (消费 Kafka ETL 任务)
poetry run kb-worker

# 6. 启动 API 服务
poetry run kb-api

# 7. 导入种子数据
python scripts/seed_knowledge.py --dir ../agent/test_data

# 8. 运行 RAGAS 评估
python -m kb.eval.ragas_eval
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

| SmartCS | kb-service |
|---------|------------|
| ES + Milvus 双写 | ES 单写（PG 真相源） |
| Python 手写 RRF | ES 原生 RRF retriever |
| pymupdf 纯文本 | docling 版式感知 |
| 人工 YAML 抽取 | LLM 自动抽取 |
| 同步阻塞 API | Kafka 异步 Worker |
| 无质量基线 | RAGAS golden query 评估 |
| 无嵌入版本治理 | model_version + 影子索引 |

## 运维 / 监控端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/health/live` | 存活探针 (k8s livenessProbe) |
| GET  | `/health/ready` | 就绪探针 — PG/ES/Redis/MinIO 全检查 |
| GET  | `/metrics` | Prometheus 指标 |
| GET  | `/api/v1/diagnostics` | 阶段耗时统计 + 依赖健康 + 熔断器状态 |
| POST | `/api/v1/admin/reindex-all?limit=100` | 重建全部 PUBLISHED 文档的 ES 索引 |
| POST | `/api/v1/admin/clear-cache` | 清空 Redis 检索缓存 (`kb:retrieve:*`) |

```bash
# 典型巡检
curl -H "X-API-Key: \$KB_API_KEY" http://kb-service:8000/api/v1/diagnostics | jq

# 数据更新后强制走 ES
curl -X POST -H "X-API-Key: \$KB_API_KEY" \
  http://kb-service:8000/api/v1/admin/clear-cache

# 嵌入模型升级后全量重建索引
curl -X POST -H "X-API-Key: \$KB_API_KEY" \
  "http://kb-service:8000/api/v1/admin/reindex-all?limit=500"
```

## 故障排查 (runbook)

### ETL 任务卡在某阶段

**症状**: 文档长期处于 `KAFKA_QUEUED` 或某一 stage 状态不变。

**定位**:
1. 看 `diagnostics.stage_stats` 阶段失败率
2. 看具体文档的 `kb_ingestion_log` (按 `document_id` 查 stage/error_message)

**常见原因**:
- `parse` 失败 — docling 未装时自动回退 pymupdf / python-docx, 看 worker 启动日志
- `embed` 失败 — EmbeddingCircuitBreaker 打开, `diagnostics.embedding_breaker.available=false`, 等后端恢复或重启 worker
- `extract` 失败 — LLM 不可达或 5xx, 检查 `LLM_BASE_URL` / `LLM_API_KEY`
- Kafka 消费积压 — `kafka-consumer-groups.sh --describe --group kb-worker`

### 检索召回率突然下降

**症状**: RAGAS 评估命中率下降, 或业务反馈"找不到原来能搜到的内容"。

**步骤**:
1. ES 集群健康: `curl es:9200/_cluster/health`
2. BM25 + kNN 索引是否都在: `curl es:9200/kb_chunks/_search -d '{"size":1}'`
3. 命中"全 0"或字段缺失 → 跑 `/api/v1/admin/reindex-all` 全量重建
4. 索引模板变更后必须重建 (mapping 不向后兼容)

### 检索慢

**症状**: `/api/v1/retrieve` p95 > 1s。

**定位**:
- `/metrics` 里的 `kb_retrieve_seconds_*` 直方图
- 拆解: ES 查询 / Reranker / LLM 抽取 — 哪段长
- 常见瓶颈: Reranker 队列堆积 / ES kNN segment 太多触发 merge / 缓存命中率低

### 嵌入服务熔断器长期打开

**症状**: 文档 ingest 一直卡在 embed 阶段, 但 TEI / Ollama 实际可用。

**原因**: 网络抖动导致连续 N 次 health_check 失败。

**处理**:
```bash
# 强制重启 worker, 熔断器状态重置
kubectl rollout restart deployment/kb-worker

# 或调阈值 (settings.yaml)
embedding:
  circuit_breaker:
    failure_threshold: 5      # 默认 3, 调大抗抖动
    recovery_threshold: 3     # 默认 2
    probe_interval_seconds: 30
```

### 重建索引后, 旧数据还在

**症状**: `reindex-all` 之后, 旧 chunk 还能搜到。

**原因**: 走 Kafka 异步, 老 chunk 还在 ES 里等被覆盖。

**处理**:
```bash
# 1. 等 Kafka 队列消化完 (看 ingestion_log)
# 2. 强制清缓存, 让 query 走新 ES
curl -X POST -H "X-API-Key: \$KB_API_KEY" .../api/v1/admin/clear-cache
# 3. 验证
curl es:9200/kb_chunks/_search -d '{"query":{"term":{"document_id":"<id>"}}}'
```

## 性能基线 (单 worker, BGE-M3, 50 万向量)

| 指标 | 参考值 | 调优手段 |
|------|--------|----------|
| 摄入吞吐 (PDF/10页) | ~30 docs/min | 增 worker 并发, docling 缓存 |
| 检索 p50 | < 80 ms | ES HNSW `ef_search` |
| 检索 p95 | < 250 ms | Reranker 切到 BGE-reranker-base |
| 嵌入 (单条) | ~12 ms | TEI 批量化 + int8 量化 |
| Rerank (top-20) | ~80 ms | TEI rerank 服务独立扩 |

## 限流与配额

- API 默认 60 req/min (`settings.security.rate_limit_per_minute`)
- 单文件上传上限 50MB (`settings.security.max_upload_size_mb`)
- 一次 reindex-all 上限 500 文档 (硬编码, 防误操作打爆 Kafka)

## Python 版本

**kb-service 强制要求 Python 3.11**. `pyahocorasick` / `uuid_utils` / `elasticsearch[async]`
等关键 C 扩展在 3.14 暂未发布 wheel, 在 3.14 环境下:

- 核心 API 可启动, 但敏感词扫描 / ORM / 集成测试会 `pytest.importorskip` 跳过
- 生产部署请用官方基础镜像 `python:3.11-slim`
- 测试如果想跑全, 必须 `poetry env use 3.11`
