"""坐席辅助编排器

接收客户消息 → 并行分发(话术/知识/质检/产品) → 汇聚 → 节流 → 推送。
纯 asyncio 实现，不依赖 LangGraph。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime

from smartcs.services.assist.alert_engine import AlertEngine
from smartcs.services.assist.product_catalog import ProductCatalog
from smartcs.services.assist.script_service import ScriptService
from smartcs.shared.config import get_settings
from smartcs.shared.models import (
    AssistPushMessage,
    AssistPushPayload,
    IntentLabel,
    KnowledgeSnippet,
    ScriptCard,
    SentimentLabel,
)

logger = logging.getLogger(__name__)


class AssistOrchestrator:
    """坐席辅助编排器

    四路并行分支，各独立超时+降级。
    """

    def __init__(
        self,
        script_service: ScriptService,
        alert_engine: AlertEngine,
        product_catalog: ProductCatalog,
        llm_client=None,
        es_client=None,
        milvus_collection=None,
        embedding_provider=None,
        embedding_breaker=None,
        reranker=None,
    ) -> None:
        self._script_service = script_service
        self._alert_engine = alert_engine
        self._product_catalog = product_catalog
        self._llm_client = llm_client
        self._es_client = es_client
        self._milvus_collection = milvus_collection
        self._embedding_provider = embedding_provider
        self._embedding_breaker = embedding_breaker
        self._reranker = reranker
        self._settings = get_settings().assist

    async def process(
        self,
        session_id: str,
        message: str,
        intent: IntentLabel,
        sentiment: SentimentLabel,
        sentiment_history: list[SentimentLabel],
        context: str = "",
        variables: dict[str, str] | None = None,
    ) -> AssistPushMessage:
        """处理单条消息，返回推送消息"""
        t_start = time.monotonic()
        variables = variables or {}

        # ── 并行分发 ──
        async def _script_branch():
            return await self._run_script_branch(intent, context, variables)

        async def _knowledge_branch():
            return await self._run_knowledge_branch(message, intent)

        async def _alert_branch():
            return self._alert_engine.check_all(
                message,
                sentiment,
                sentiment_history,
                self._settings.sentiment_trend_window,
            )

        async def _product_branch():
            return await self._run_product_branch(intent)

        script_result, knowledge_result, alert_result, product_result = await _parallel_dispatch(
            _script_branch(),
            _knowledge_branch(),
            _alert_branch(),
            _product_branch(),
            timeouts=(
                self._settings.script_timeout_ms / 1000,
                self._settings.knowledge_timeout_ms / 1000,
                self._settings.alert_timeout_ms / 1000,
                self._settings.product_timeout_ms / 1000,
            ),
        )

        # ── 组装推送载荷 ──
        scripts = [ScriptCard(**s) if isinstance(s, dict) else s for s in script_result]
        knowledge = [KnowledgeSnippet(**k) if isinstance(k, dict) else k for k in knowledge_result]
        alerts = alert_result
        products = product_result

        payload = AssistPushPayload(
            scripts=scripts,
            knowledge=knowledge,
            alerts=alerts,
            recommendations=products,
        )

        elapsed = (time.monotonic() - t_start) * 1000
        logger.info(
            "assist orchestration session=%s intent=%s scripts=%d knowledge=%d alerts=%d products=%d elapsed=%.1fms",
            session_id,
            intent.value,
            len(scripts),
            len(knowledge),
            len(alerts),
            len(products),
            elapsed,
        )

        return AssistPushMessage(
            session_id=session_id,
            timestamp=datetime.now(),
            trigger="customer_message",
            payload=payload,
        )

    async def _run_script_branch(self, intent: IntentLabel, context: str, variables: dict[str, str]) -> list[dict]:
        scripts = self._script_service.retrieve(intent, top_k=self._settings.max_scripts_per_push)
        if not scripts:
            return []
        result = []
        for s in scripts:
            resolved = self._script_service.resolve_variables(s, variables)
            # LLM 润色仅在 LLM 可用且超时充裕时执行
            if self._llm_client and self._settings.script_timeout_ms > 1000:
                with contextlib.suppress(Exception):
                    resolved = await self._script_service.polish(
                        resolved,
                        context,
                        self._llm_client,
                        timeout_ms=self._settings.script_timeout_ms - 50,
                    )
            result.append(
                {
                    "script_id": s["script_id"],
                    "content": resolved,
                    "tags": s.get("tags", []),
                    "priority": s.get("priority", 5),
                }
            )
        return result

    async def _run_knowledge_branch(self, message: str, intent: IntentLabel) -> list[dict]:
        if not self._es_client:
            return []
        try:
            from smartcs.services.common.retrieval import retrieve
            from smartcs.shared.models import RetrieveRequest

            # 如果 embedding 不可用，直接用 BM25 避免等待 HTTP 超时
            embedding_ok = (
                self._embedding_provider is not None
                and getattr(self, "_embedding_breaker", None) is not None
                and self._embedding_breaker.is_available
            )
            search_type = "hybrid" if embedding_ok else "bm25_only"
            logger.debug(
                "knowledge branch: embedding_ok=%s search_type=%s query=%s",
                embedding_ok,
                search_type,
                message[:30],
            )

            req = RetrieveRequest(
                query=message,
                top_k=self._settings.max_knowledge_per_push,
                rerank=embedding_ok,  # embedding 不可用时也不做 rerank
                search_type=search_type,
            )
            resp = await retrieve(
                request=req,
                es_client=self._es_client,
                milvus_collection=self._milvus_collection,
                embedding_provider=self._embedding_provider,
                reranker=self._reranker,
            )
            return [
                {
                    "chunk_id": c.chunk_id,
                    "summary": c.content[:200],
                    "source": c.source_doc,
                    "confidence": "high" if c.score > 0.8 else "medium" if c.score > 0.5 else "low",
                }
                for c in resp.results
            ]
        except Exception as e:
            logger.warning("知识检索失败: %s", e)
            return []

    async def _run_product_branch(self, intent: IntentLabel) -> list[dict]:
        products = self._product_catalog.match(
            intent,
            top_k=self._settings.max_recommendations_per_push,
        )
        return [
            {
                "product_id": p.product_id,
                "product_name": p.product_name,
                "reason": p.description,
                "script_suggestion": p.script_template,
                "risk_tip": p.risk_tip,
                "eligibility_match": True,
            }
            for p in products
        ]

    def should_throttle(self, session_id: str) -> bool:
        """节流检查已统一到 PushTracker（Redis 持久化，跨实例一致）

        此方法保留向后兼容，始终返回 False（不节流）。
        实际节流由 Assist Engine 中的 should_show() + PushTracker 负责。
        """
        return False

    def force_reset_throttle(self, session_id: str) -> None:
        """重置节流计时器（已由 PushTracker 管理，此方法为空操作）"""
        pass


async def _parallel_dispatch(
    script_coro,
    knowledge_coro,
    alert_coro,
    product_coro,
    timeouts: tuple[float, float, float, float],
) -> tuple[list, list, list, list]:
    """并行执行四路分支，各独立超时，单路失败不影响其他"""

    async def _run_with_timeout(coro, timeout: float, label: str, default):
        try:
            if timeout > 0:
                return await asyncio.wait_for(coro, timeout=timeout)
            return await coro
        except TimeoutError:
            logger.warning("分支 %s 超时 (%.1fs)，触发降级", label, timeout)
            return default
        except Exception as e:
            logger.warning("分支 %s 异常: %s，触发降级", label, e)
            return default

    results = await asyncio.gather(
        _run_with_timeout(script_coro, timeouts[0], "script", []),
        _run_with_timeout(knowledge_coro, timeouts[1], "knowledge", []),
        _run_with_timeout(alert_coro, timeouts[2], "alert", []),
        _run_with_timeout(product_coro, timeouts[3], "product", []),
    )
    return tuple(results)
