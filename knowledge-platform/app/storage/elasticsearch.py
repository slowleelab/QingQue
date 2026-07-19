"""Elasticsearch 客户端管理

ES 职责：
1. BM25 + IK 中文分词关键词召回
2. kNN 向量召回 (dense_vector + HNSW)
3. 原生 RRF retriever 服务端融合 (ES 8.14+)

ES 是可从 PG 重建的派生索引，非真相源。
"""

from __future__ import annotations

import logging
from typing import Any

from elasticsearch import AsyncElasticsearch

from app.config import get_settings

logger = logging.getLogger(__name__)

_client: AsyncElasticsearch | None = None


async def init_es() -> AsyncElasticsearch | None:
    """初始化 ES 异步客户端"""
    global _client
    settings = get_settings()
    es_kwargs: dict[str, Any] = {"hosts": [settings.elasticsearch.hosts]}
    if settings.elasticsearch.username:
        es_kwargs["basic_auth"] = (settings.elasticsearch.username, settings.elasticsearch.password)
    es_kwargs["verify_certs"] = settings.elasticsearch.verify_certs
    client = AsyncElasticsearch(**es_kwargs)
    try:
        await client.ping()
        _client = client
        logger.info("Elasticsearch 连接成功: %s", settings.elasticsearch.hosts)
        return client
    except Exception as e:
        logger.warning("Elasticsearch 连接失败，将使用降级模式: %s", e)
        _client = None
        return None


async def close_es() -> None:
    """关闭 ES 客户端"""
    global _client
    if _client:
        await _client.close()
        _client = None
        logger.info("Elasticsearch 客户端已关闭")


def get_es() -> AsyncElasticsearch | None:
    """获取 ES 客户端"""
    return _client
