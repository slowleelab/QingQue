"""AI 服务执行器 — 确定性实现

并行调用话术检索 + RAG 检索 + 合规检查，LLM 仅用于话术润色。
不依赖任何 Agent 框架。

通路:
- 快速通路: 话术 Top1 得分 > 0.9 → 直接返回话术（跳过深度检索）
- 深度通路: 话术无匹配 → RAG 检索 → 重排序 → 合规过滤
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from smartcs.services.assist.arbitrator import GlobalArbitrator

logger = logging.getLogger(__name__)


class AIExecutor:
    """AI 服务执行器 — 确定性实现

    并行调用三路，LLM 仅参与话术润色。
    """

    def __init__(
        self,
        script_service: Any = None,
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
        self._last_result: dict[str, Any] = {}

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        """执行 AI 执行器

        Returns:
            {ui_schema, latency_ms, degraded, degradation_type, path, fast_path_hit}
        """
        t0 = time.monotonic()
        message = kwargs.get("message", "")
        intent = kwargs.get("intent", "faq")

        # PII 脱敏日志
        arbitrator = GlobalArbitrator()
        masked_msg = arbitrator.mask_pii(message) if message else ""
        logger.debug("AIExecutor: intent=%s msg=%s", intent, masked_msg[:50])

        try:
            # ── 并行三路 ──
            script_task = self._retrieve_scripts(intent)
            rag_task = self._search_rag(message)
            compliance_task = self._check_compliance(message)

            scripts_result, rag_result, compliance_result = await asyncio.gather(
                script_task,
                rag_task,
                compliance_task,
            )

            # ── 通路判断 ──
            top1_score = scripts_result.get("top1_score", 0.0)
            fast_hit = top1_score > 0.9

            if fast_hit:
                # 快速通路: 话术直接命中
                scripts = scripts_result.get("scripts", [])
                if scripts and self._llm:
                    try:
                        polished = await asyncio.wait_for(
                            self._script_service.polish(scripts[0], message, self._llm, timeout_ms=450),
                            timeout=1.0,
                        )
                        scripts[0]["content"] = polished
                    except Exception:
                        pass  # 润色失败用原文

                ui_schema = {
                    "scripts": scripts,
                    "knowledge": [],
                    "alerts": compliance_result.get("alerts", []),
                }
            else:
                # 深度通路: RAG 检索 + 归一化 + 合规过滤
                knowledge = rag_result.get("knowledge", [])
                # 重排序
                if self._reranker and knowledge:
                    try:
                        texts = [k.get("summary", "") for k in knowledge]
                        results = self._reranker.rerank(query=message, documents=texts)
                        for r in results:
                            if 0 <= r.index < len(knowledge):
                                knowledge[r.index]["rerank_score"] = r.relevance_score
                        knowledge.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
                    except Exception as e:
                        logger.warning("重排序失败: %s", e)

                ui_schema = {
                    "scripts": scripts_result.get("scripts", []),
                    "knowledge": knowledge[:3],
                    "alerts": compliance_result.get("alerts", []),
                }

            # ── 合规告警处理 ──
            degraded = False
            degradation_type = ""
            alerts = compliance_result.get("alerts", [])
            if any(a.get("level") == "critical" for a in alerts):
                degraded = True
                degradation_type = "safe_fallback"
                ui_schema = {
                    "fallback": "抱歉，服务暂时不可用，请稍后再试。",
                    "scripts": [],
                    "knowledge": [],
                    "alerts": alerts,
                }

            # PII 脱敏输出
            ui_schema = arbitrator._mask_pii_recursive(ui_schema)

            elapsed = int((time.monotonic() - t0) * 1000)
            result: dict[str, Any] = {
                "ui_schema": ui_schema,
                "latency_ms": elapsed,
                "degraded": degraded,
                "degradation_type": degradation_type,
                "path": "fast" if fast_hit else "deep",
                "fast_path_hit": fast_hit,
            }
            self._last_result = result
            return result

        except Exception as e:
            logger.warning("AIExecutor 异常: %s", e)
            elapsed = int((time.monotonic() - t0) * 1000)
            fallback_msg = "抱歉，服务暂时不可用，请稍后再试。"
            fallback = {
                "ui_schema": {"fallback": fallback_msg, "scripts": [], "knowledge": [], "alerts": []},
                "latency_ms": elapsed,
                "degraded": True,
                "degradation_type": "safe_fallback",
                "path": "deep",
                "fast_path_hit": False,
            }
            self._last_result = fallback
            return fallback

    # ── 三路并行方法 ──

    async def _retrieve_scripts(self, intent: str) -> dict[str, Any]:
        """话术检索"""
        if self._script_service is None:
            return {"scripts": [], "top1_score": 0.0}

        try:
            from smartcs.shared.models import IntentLabel

            intent_label = IntentLabel(intent)
        except ValueError:
            intent_label = IntentLabel.FAQ  # type: ignore[assignment]

        scripts = self._script_service.retrieve(intent_label, top_k=3)
        if not scripts:
            return {"scripts": [], "top1_score": 0.0}

        result = []
        top1 = 0.0
        for i, s in enumerate(scripts):
            score = s.get("score", 0.0) if isinstance(s, dict) else 0.0
            if i == 0:
                top1 = score
            result.append(
                {
                    "script_id": s.get("script_id", "") if isinstance(s, dict) else getattr(s, "script_id", ""),
                    "content": s.get("content", "") if isinstance(s, dict) else getattr(s, "content", ""),
                    "score": score,
                    "tags": s.get("tags", []) if isinstance(s, dict) else getattr(s, "tags", []),
                }
            )
        return {"scripts": result, "top1_score": top1}

    async def _search_rag(self, query: str) -> dict[str, Any]:
        """RAG 知识检索"""
        if self._es_client is None:
            return {"knowledge": []}

        try:
            from smartcs.services.common.retrieval import retrieve
            from smartcs.shared.models import RetrieveRequest

            embedding_ok = (
                self._embedding is not None
                and self._embedding_breaker is not None
                and self._embedding_breaker.is_available
            )
            search_type = "hybrid" if embedding_ok else "bm25_only"

            resp = await retrieve(
                request=RetrieveRequest(query=query, top_k=5, rerank=False, search_type=search_type),
                es_client=self._es_client,
                milvus_collection=self._milvus,
                embedding_provider=self._embedding,
                reranker=self._reranker,
            )
            return {
                "knowledge": [
                    {
                        "chunk_id": getattr(c, "chunk_id", ""),
                        "summary": c.content[:200] if c.content else "",
                        "source": getattr(c, "source_doc", ""),
                        "confidence": "high" if c.score > 0.8 else "medium" if c.score > 0.5 else "low",
                    }
                    for c in resp.results
                ]
            }
        except Exception as e:
            logger.warning("RAG 检索失败: %s", e)
            return {"knowledge": []}

    async def _check_compliance(self, text: str) -> dict[str, Any]:
        """合规检查"""
        if self._alert_engine is None:
            return {"alerts": [], "passed": True}

        try:
            alerts = self._alert_engine.check_compliance(text)
            return {
                "alerts": [{"level": a.get("level", "info"), "message": a.get("message", "")} for a in alerts],
                "passed": not any(a.get("level") == "critical" for a in alerts),
            }
        except Exception as e:
            logger.warning("合规检查异常: %s", e)
            return {"alerts": [], "passed": True}

    @property
    def graph(self):
        """兼容旧版 AIExecutorDAG.graph 属性（测试/可视化用）"""
        return None
