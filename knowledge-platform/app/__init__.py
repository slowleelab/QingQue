"""knowledge-platform — 银行知识数据平台

独立微服务，从 SmartCS 抽离的数据处理全链路：
- 离线 ETL：采集 → 解析(docling) → 清洗 → LLM抽取 → 分块 → 向量化 → 存储(PG+ES)
- 在线检索：ES 原生 RRF (BM25+IK ‖ kNN) + Reranker 精排
- 异步管线：Kafka 任务队列 + 独立 Worker 进程
"""

__version__ = "0.1.0"
