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

    # ── I1-C2: 多租户 + 角色 + 排除语义 + 超时 ──
    # tenant_id: 多租户隔离 (从 principal 自动注入, 业务侧可显式覆盖)
    tenant_id: str | None = None
    # actor_roles: 当前用户角色列表 (与 KbDocument.allowed_roles 做 ES terms 命中)
    actor_roles: list[str] = Field(default_factory=list)
    # exclude: 排除语义 (P2-1 顺手, must_not 子句)
    # 字段值可以是单值或 list, 统一转 ES must_not terms
    exclude: dict = Field(default_factory=dict)
    # timeout_ms: 总超时, 引擎按阶段分摊 (P1-3 顺手)
    timeout_ms: int = 1500


class RetrieveResponse(BaseModel):
    """检索响应"""

    results: list[RetrievedChunk] = Field(default_factory=list)
    total_candidates: int = 0
    latency_ms: int = 0
    # ── I1-C2: 降级标记 ──
    degraded: bool = False
    degraded_stages: list[str] = Field(default_factory=list)


class RerankResult(BaseModel):
    """重排序结果"""

    index: int
    relevance_score: float
    text: str
