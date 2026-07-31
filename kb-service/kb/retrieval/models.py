"""检索数据模型"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RetrievedChunk(BaseModel):
    """检索到的知识块"""

    chunk_id: str
    content: str
    score: float
    source_doc: str
    metadata: dict = Field(default_factory=dict)


class RetrieveRequest(BaseModel):
    """检索请求"""

    query: str
    top_k: int = 5
    filters: dict = Field(default_factory=dict)
    rerank: bool = True
    search_type: Literal["hybrid", "bm25_only", "vector_only"] = "hybrid"
    rrf_k: int | None = None

    # 银行合规
    user_role: str | None = None
    include_expired: bool = False

    # 嵌入版本灰度：指定 model_version 过滤，用于影子索引测试
    model_version: str | None = None


class RetrieveResponse(BaseModel):
    """检索响应"""

    results: list[RetrievedChunk] = Field(default_factory=list)
    total_candidates: int = 0
    latency_ms: int = 0


class RerankResult(BaseModel):
    """重排序结果"""

    index: int
    relevance_score: float
    text: str
