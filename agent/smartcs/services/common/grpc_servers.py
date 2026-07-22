"""gRPC AI 服务端实现

包装现有的 Python 实现（classifier/retrieval/safety），暴露为标准 gRPC 服务。
可作为独立进程运行，也可嵌入 FastAPI lifespan 启动。

三个服务:
  - ClassificationService (:50051) — 意图分类 + 实体抽取 + 情感分析
  - RetrievalService    (:50052) — 混合检索 BM25+向量+RRF
  - SafetyFilterService (:50053) — 输入/输出过滤 + 合规校验
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import grpc.aio

from generated.proto import classification_pb2, classification_pb2_grpc
from generated.proto import retrieval_pb2, retrieval_pb2_grpc
from generated.proto import safety_pb2, safety_pb2_grpc

if TYPE_CHECKING:
    from elasticsearch import AsyncElasticsearch
    from pymilvus import Collection

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# ClassificationService — 包装 classifier.py
# ══════════════════════════════════════════════════════════════


class ClassificationServicer(classification_pb2_grpc.ClassificationServiceServicer):
    """意图分类 gRPC 服务"""

    def __init__(self, classifier: object) -> None:
        self._classifier = classifier

    async def Classify(self, request: classification_pb2.ClassifyRequest, context: grpc.aio.ServicerContext) -> classification_pb2.ClassifyResponse:
        try:
            from smartcs.shared.models import ClassifyType, IntentLabel, SentimentLabel

            types = {t for t in request.classify_types}
            intent = intent_result_pb = None
            entities_pb: list = []
            sentiment_pb = None

            # 调用现有的统一 classify 方法
            intent_result, entities, sentiment, _ = await self._classifier.classify(request.text)

            if ClassifyType.INTENT in types or not types:
                intent_result_pb = classification_pb2.IntentResult(
                    primary_intent=intent_result.primary_intent.value,
                    primary_confidence=intent_result.primary_confidence,
                )
                if intent_result.secondary_intents:
                    for si in intent_result.secondary_intents:
                        intent_result_pb.secondary_intents.append(
                            classification_pb2.IntentItem(intent=si.intent.value, confidence=si.confidence)
                        )

            if ClassifyType.ENTITY in types or not types:
                for e in entities:
                    entities_pb.append(classification_pb2.Entity(entity_type=e.entity_type, value=e.value, confidence=e.confidence))

            if ClassifyType.SENTIMENT in types or not types:
                sentiment_pb = classification_pb2.SentimentResult(label=sentiment.value)

            return classification_pb2.ClassifyResponse(
                intent=intent_result_pb,
                entities=entities_pb,
                sentiment=sentiment_pb,
                latency_ms=0,
            )
        except Exception as e:
            await context.abort(grpc.StatusCode.INTERNAL, str(e))
            return classification_pb2.ClassifyResponse()  # unreachable

    async def ClassifyIntent(self, request: classification_pb2.IntentRequest, context: grpc.aio.ServicerContext) -> classification_pb2.IntentResponse:
        try:
            intents, _ = await self._classifier.classify_intent(request.text)
            resp = classification_pb2.IntentResponse(primary_intent=intents[0].value if intents else "faq")
            return resp
        except Exception as e:
            await context.abort(grpc.StatusCode.INTERNAL, str(e))
            return classification_pb2.IntentResponse()


# ══════════════════════════════════════════════════════════════
# RetrievalService — 包装 retrieval.py
# ══════════════════════════════════════════════════════════════


class RetrievalServicer(retrieval_pb2_grpc.RetrievalServiceServicer):
    """混合检索 gRPC 服务"""

    def __init__(self, es_client: AsyncElasticsearch | None = None, milvus_collection: Collection | None = None, embedding_provider: object | None = None, reranker: object | None = None) -> None:
        self._es = es_client
        self._milvus = milvus_collection
        self._embedding = embedding_provider
        self._reranker = reranker

    async def Retrieve(self, request: retrieval_pb2.RetrieveRequest, context: grpc.aio.ServicerContext) -> retrieval_pb2.RetrieveResponse:
        try:
            from smartcs.services.common.retrieval import retrieve
            from smartcs.shared.models import RetrieveRequest

            req = RetrieveRequest(query=request.query, top_k=request.top_k or 5, rerank=request.rerank, search_type=request.search_type or "hybrid")
            resp = await retrieve(
                request=req,
                es_client=self._es,
                milvus_collection=self._milvus,
                embedding_provider=self._embedding,
                reranker=self._reranker,
            )
            results_pb = []
            for r in resp.results:
                results_pb.append(
                    retrieval_pb2.RetrievalResult(
                        chunk_id=r.chunk_id,
                        content=r.content,
                        score=r.score,
                        source_doc=r.source_doc if hasattr(r, "source_doc") else "",
                    )
                )
            return retrieval_pb2.RetrieveResponse(results=results_pb)
        except Exception as e:
            await context.abort(grpc.StatusCode.INTERNAL, str(e))
            return retrieval_pb2.RetrieveResponse()

    async def RetrieveBM25(self, request: retrieval_pb2.BM25Request, context: grpc.aio.ServicerContext) -> retrieval_pb2.RetrieveResponse:
        return await self.Retrieve(
            retrieval_pb2.RetrieveRequest(query=request.query, top_k=request.top_k or 3, search_type="bm25_only"),
            context,
        )

    async def RetrieveVector(self, request: retrieval_pb2.VectorRequest, context: grpc.aio.ServicerContext) -> retrieval_pb2.RetrieveResponse:
        return await self.Retrieve(
            retrieval_pb2.RetrieveRequest(query=request.query, top_k=request.top_k or 3, search_type="vector_only"),
            context,
        )


# ══════════════════════════════════════════════════════════════
# SafetyFilterService — 包装 safety.py
# ══════════════════════════════════════════════════════════════


class SafetyFilterServicer(safety_pb2_grpc.SafetyFilterServiceServicer):
    """安全过滤 gRPC 服务"""

    def __init__(self, safety_filter_obj: object | None = None) -> None:
        self._safety = safety_filter_obj

    async def FilterInput(self, request: safety_pb2.FilterInputRequest, context: grpc.aio.ServicerContext) -> safety_pb2.FilterInputResponse:
        try:
            from smartcs.shared.safety import safety_filter

            sf = self._safety or safety_filter
            result = sf.filter(request.text)
            return safety_pb2.FilterInputResponse(
                passed=result.get("passed", True),
                reason=result.get("reason", ""),
                masked_text=result.get("masked_text", request.text),
            )
        except Exception as e:
            await context.abort(grpc.StatusCode.INTERNAL, str(e))
            return safety_pb2.FilterInputResponse()

    async def FilterOutput(self, request: safety_pb2.FilterOutputRequest, context: grpc.aio.ServicerContext) -> safety_pb2.FilterOutputResponse:
        try:
            from smartcs.shared.safety import safety_filter

            sf = self._safety or safety_filter
            result = sf.filter(request.text)
            return safety_pb2.FilterOutputResponse(
                passed=result.get("passed", True),
                blocked=result.get("blocked", False),
                filtered_text=result.get("masked_text", request.text),
            )
        except Exception as e:
            await context.abort(grpc.StatusCode.INTERNAL, str(e))
            return safety_pb2.FilterOutputResponse()

    async def CheckCompliance(self, request: safety_pb2.ComplianceRequest, context: grpc.aio.ServicerContext) -> safety_pb2.ComplianceResponse:
        try:
            from smartcs.services.assist.alert_engine import AlertEngine

            engine = AlertEngine()
            alerts = engine.check_compliance(request.text)
            has_critical = any(a.get("level") == "critical" for a in alerts) if isinstance(alerts, list) else False
            return safety_pb2.ComplianceResponse(
                compliant=not has_critical,
                alerts=[safety_pb2.ComplianceAlert(level=a.get("level", "info"), message=a.get("message", "")) for a in alerts] if isinstance(alerts, list) else [],
            )
        except Exception as e:
            await context.abort(grpc.StatusCode.INTERNAL, str(e))
            return safety_pb2.ComplianceResponse()


# ══════════════════════════════════════════════════════════════
# 服务器启动器
# ══════════════════════════════════════════════════════════════


async def serve_classification(classifier: object, port: int = 50051) -> grpc.aio.Server:
    server = grpc.aio.server()
    classification_pb2_grpc.add_ClassificationServiceServicer_to_server(ClassificationServicer(classifier), server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    logger.info("ClassificationService gRPC → :%d", port)
    return server


async def serve_retrieval(es_client: object | None, milvus_collection: object | None, embedding_provider: object | None, reranker: object | None, port: int = 50052) -> grpc.aio.Server:
    server = grpc.aio.server()
    retrieval_pb2_grpc.add_RetrievalServiceServicer_to_server(
        RetrievalServicer(es_client, milvus_collection, embedding_provider, reranker), server
    )
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    logger.info("RetrievalService gRPC → :%d", port)
    return server


async def serve_safety(safety_obj: object | None = None, port: int = 50053) -> grpc.aio.Server:
    server = grpc.aio.server()
    safety_pb2_grpc.add_SafetyFilterServiceServicer_to_server(SafetyFilterServicer(safety_obj), server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    logger.info("SafetyFilterService gRPC → :%d", port)
    return server
