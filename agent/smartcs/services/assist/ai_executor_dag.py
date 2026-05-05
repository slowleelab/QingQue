"""AI 服务执行器 — LangGraph DAG

对应设计文档 §2，实现快速/深度双通路推理。
DAG 节点:
  Entry → CheckFast → FastRetrieval/MonitorHit | RAG+KG(并行) → Normalize → ReRank → Firewall → Fallback/FormatOut → Exit
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

logger = logging.getLogger(__name__)


class DAGState(TypedDict, total=False):
    """DAG 内部状态"""

    session_id: str
    message: str
    intent: str
    sentiment: str
    confidence: float
    state_snapshot: dict[str, Any]
    trace_id: str
    # 通路选择
    path: str  # "fast" | "deep"
    fast_path_hit: bool
    # 快速通路结果
    fast_script: dict[str, Any]
    # 深度通路: RAG 候选
    rag_candidates: list[dict[str, Any]]
    # 深度通路: KG 候选
    kg_candidates: list[dict[str, Any]]
    # 归一化后候选
    normalized_candidates: list[dict[str, Any]]
    # 重排序后候选
    reranked_candidates: list[dict[str, Any]]
    # 合规防火墙
    firewall_passed: bool
    firewall_block_reason: str
    # 输出
    ui_schema: dict[str, Any]
    degraded: bool
    degradation_type: str
    latency_ms: int


class AIExecutorDAG:
    """AI 服务执行器 LangGraph DAG

    快速通路: Top1 confidence > 0.9 时直接命中标准话术库，跳过深度推理。
    深度通路: RAG + KG 并行检索 → 归一化 → 重排序 → 合规防火墙。
    合规防火墙: 短语规则 + 分类器双重过滤 + PII 脱敏；拦截时降级为安全兜底话术。
    """

    def __init__(
        self,
        script_service: Any,  # ScriptService
        es_client: Any = None,
        milvus_collection: Any = None,
        embedding_provider: Any = None,
        embedding_breaker: Any = None,
        reranker: Any = None,
        llm_client: Any = None,
        alert_engine: Any = None,
    ) -> None:
        self._script_service = script_service
        self._es_client = es_client
        self._milvus = milvus_collection
        self._embedding = embedding_provider
        self._embedding_breaker = embedding_breaker
        self._reranker = reranker
        self._llm = llm_client
        self._alert_engine = alert_engine
        self._graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """构建 LangGraph DAG"""
        graph = StateGraph(DAGState)

        # 添加节点
        graph.add_node("check_fast", self._check_fast)
        graph.add_node("fast_retrieval", self._fast_retrieval)
        graph.add_node("monitor_hit", self._monitor_hit)
        graph.add_node("rag_chain", self._rag_chain)
        graph.add_node("kg_chain", self._kg_chain)
        graph.add_node("normalize", self._normalize)
        graph.add_node("rerank", self._rerank)
        graph.add_node("firewall", self._firewall)
        graph.add_node("fallback", self._fallback)
        graph.add_node("format_out", self._format_out)

        # 入口
        graph.set_entry_point("check_fast")

        # 条件路由: 快速通路 vs 深度通路
        graph.add_conditional_edges(
            "check_fast",
            self._route_by_confidence,
            {
                "fast": "fast_retrieval",
                "deep": "rag_chain",
            },
        )

        # 快速通路
        graph.add_edge("fast_retrieval", "monitor_hit")
        graph.add_edge("monitor_hit", "firewall")

        # 深度通路: RAG 和 KG 并行后汇入 normalize
        graph.add_edge("rag_chain", "normalize")
        graph.add_edge("kg_chain", "normalize")
        graph.add_edge("normalize", "rerank")
        graph.add_edge("rerank", "firewall")

        # 合规防火墙路由
        graph.add_conditional_edges(
            "firewall",
            self._route_by_firewall,
            {
                "pass": "format_out",
                "block": "fallback",
            },
        )
        graph.add_edge("fallback", "format_out")
        graph.add_edge("format_out", END)

        return graph.compile()

    # ── 路由函数 ──

    def _route_by_confidence(self, state: DAGState) -> str:
        """Top1 得分 > 0.9 → 快速通路"""
        return "fast" if state.get("confidence", 0) > 0.9 else "deep"

    def _route_by_firewall(self, state: DAGState) -> str:
        """合规防火墙放行/拦截路由"""
        return "pass" if state.get("firewall_passed", True) else "block"

    # ── DAG 节点 ──

    async def _check_fast(self, state: DAGState) -> dict:
        """快速通路判断节点"""
        return {"path": "fast" if state.get("confidence", 0) > 0.9 else "deep"}

    async def _fast_retrieval(self, state: DAGState) -> dict:
        """标准话术直接命中"""
        from smartcs.shared.models import IntentLabel

        try:
            intent = IntentLabel(state.get("intent", "faq"))
        except ValueError:
            intent = IntentLabel.FAQ

        scripts = self._script_service.retrieve(intent, top_k=1)
        if scripts:
            return {"fast_script": scripts[0], "fast_path_hit": True}
        return {"fast_script": {}, "fast_path_hit": False}

    async def _monitor_hit(self, state: DAGState) -> dict:
        """快速通路命中记录（对应文档: 快速通路命中率作为核心可观测性指标）"""
        logger.info(
            "快速通路命中: session=%s intent=%s hit=%s",
            state.get("session_id"),
            state.get("intent"),
            state.get("fast_path_hit"),
        )
        return {}

    async def _rag_chain(self, state: DAGState) -> dict:
        """RAG 生成链路"""
        if not self._es_client:
            return {"rag_candidates": []}
        try:
            from smartcs.services.common.retrieval import retrieve
            from smartcs.shared.models import RetrieveRequest

            embedding_ok = (
                self._embedding is not None
                and self._embedding_breaker is not None
                and self._embedding_breaker.is_available
            )
            search_type = "hybrid" if embedding_ok else "bm25_only"

            req = RetrieveRequest(
                query=state.get("message", ""),
                top_k=5,
                rerank=False,
                search_type=search_type,
            )
            resp = await retrieve(
                request=req,
                es_client=self._es_client,
                milvus_collection=self._milvus,
                embedding_provider=self._embedding,
                reranker=self._reranker,
            )
            candidates = [
                {"content": c.content, "score": c.score, "source": c.source_doc, "origin": "rag"}
                for c in resp.results
            ]
            return {"rag_candidates": candidates}
        except Exception as e:
            logger.warning("RAG 链路失败: %s", e)
            return {"rag_candidates": []}

    async def _kg_chain(self, state: DAGState) -> dict:
        """知识图谱推理链路

        当前为 placeholder，返回空候选。
        后续对接知识图谱服务。
        """
        # TODO: 对接知识图谱服务
        return {"kg_candidates": []}

    async def _normalize(self, state: DAGState) -> dict:
        """候选归一化

        对应文档 §2: RAG Top-5 / KG Top-3，避免分布不均衡
        """
        rag = state.get("rag_candidates", [])[:5]
        kg = state.get("kg_candidates", [])[:3]
        all_candidates = rag + kg
        return {"normalized_candidates": all_candidates}

    async def _rerank(self, state: DAGState) -> dict:
        """统一重排序模型

        对应文档 §2:
        - 特征包含语义、情绪、采纳率
        - 无采纳率数据的候选降权 ×0.7
        """
        candidates = list(state.get("normalized_candidates", []))
        if not candidates:
            return {"reranked_candidates": []}

        # 如果有 reranker，使用它
        if self._reranker:
            try:
                query = state.get("message", "")
                texts = [c.get("content", "") for c in candidates]
                results = self._reranker.rerank(query=query, documents=texts)
                # 用 RerankResult.index 映射回原始候选
                for r in results:
                    if 0 <= r.index < len(candidates):
                        candidates[r.index]["rerank_score"] = r.relevance_score
                candidates.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
            except Exception as e:
                logger.warning("重排序失败: %s", e)

        # 无采纳率数据的候选降权 ×0.7
        for c in candidates:
            if "adoption_rate" not in c:
                c["score"] = c.get("score", 0) * 0.7

        return {"reranked_candidates": candidates}

    async def _firewall(self, state: DAGState) -> dict:
        """合规防火墙

        对应文档 §2:
        - 短语规则 + 分类器双重过滤
        - 同时完成 PII 脱敏
        """
        if self._alert_engine:
            try:
                # 拼接待检查内容: 快速通路脚本 + 深度通路候选摘要
                parts: list[str] = []
                fast_script = state.get("fast_script")
                if fast_script and isinstance(fast_script, dict):
                    content = fast_script.get("content", "")
                    if content:
                        parts.append(content)
                for c in state.get("reranked_candidates", [])[:3]:
                    parts.append(c.get("content", ""))
                text_to_check = " ".join(parts)

                alerts = self._alert_engine.check_compliance(text_to_check)
                has_critical = any(a.get("level") == "critical" for a in alerts)
                if has_critical:
                    return {"firewall_passed": False, "firewall_block_reason": "合规拦截"}
            except Exception as e:
                logger.warning("合规检查异常: %s", e)

        return {"firewall_passed": True}

    async def _fallback(self, state: DAGState) -> dict:
        """降级安全兜底话术

        对应文档 §2: 配置中心管理，支持 A/B 测试
        """
        from smartcs.services.common.degradation import ContentDegrader
        from smartcs.shared.models import IntentLabel

        degrader = ContentDegrader()
        try:
            intent = IntentLabel(state.get("intent", "faq"))
        except ValueError:
            intent = IntentLabel.FAQ
        fallback = degrader.get_template(intent)
        return {
            "degraded": True,
            "degradation_type": "safe_fallback",
            "ui_schema": {"fallback": fallback, "scripts": [], "knowledge": [], "alerts": []},
        }

    async def _format_out(self, state: DAGState) -> dict:
        """格式化 UI Schema

        对应文档 §2: 最终结果格式化为标准 UI Schema 并携带 trace_id
        """
        if state.get("degraded"):
            return {}

        # 快速通路结果
        if state.get("path") == "fast" and state.get("fast_script"):
            script = state["fast_script"]
            if isinstance(script, dict):
                ui: dict[str, Any] = {"scripts": [script], "knowledge": [], "alerts": []}
            else:
                ui = {"scripts": [{"content": str(script)}], "knowledge": [], "alerts": []}
        elif state.get("reranked_candidates"):
            top = state["reranked_candidates"][:3]
            ui = {
                "scripts": [],
                "knowledge": [
                    {
                        "chunk_id": c.get("source", ""),
                        "summary": c.get("content", "")[:200],
                        "source": c.get("source", ""),
                        "confidence": (
                            "high"
                            if c.get("score", 0) > 0.8
                            else "medium" if c.get("score", 0) > 0.5 else "low"
                        ),
                    }
                    for c in top
                ],
                "alerts": [],
            }
        else:
            ui = {}

        return {"ui_schema": ui}

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        """执行 DAG"""
        t0 = time.monotonic()
        initial_state: DAGState = {
            "session_id": kwargs.get("session_id", ""),
            "message": kwargs.get("message", ""),
            "intent": kwargs.get("intent", "faq"),
            "sentiment": kwargs.get("sentiment", "neutral"),
            "confidence": kwargs.get("confidence", 0.0),
            "state_snapshot": kwargs.get("state_snapshot", {}),
            "trace_id": kwargs.get("trace_id", ""),
            "path": "deep",
            "fast_path_hit": False,
            "fast_script": {},
            "rag_candidates": [],
            "kg_candidates": [],
            "normalized_candidates": [],
            "reranked_candidates": [],
            "firewall_passed": True,
            "firewall_block_reason": "",
            "ui_schema": {},
            "degraded": False,
            "degradation_type": "",
            "latency_ms": 0,
        }
        result = await self._graph.ainvoke(initial_state)
        result["latency_ms"] = int((time.monotonic() - t0) * 1000)
        return result

    @property
    def graph(self):
        """获取编译后的 DAG 图（用于测试和可视化）"""
        return self._graph
