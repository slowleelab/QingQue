"""机器人服务 HTTP API 路由"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid as uuid_module

from fastapi import APIRouter, File, Form, Query, Request, UploadFile

from smartcs.services.common.deps import (
    DbSession,
    EmbeddingBreakerDep,
    ESClientDep,
    MilvusCollectionDep,
    MinioClientDep,
    RerankerProviderDep,
)
from smartcs.services.common.retrieval import retrieve
from smartcs.shared.exceptions import DocumentFormatError
from smartcs.shared.models import (
    ChatSendRequest,
    ChatSendResponse,
    PollResponse,
    RetrieveRequest,
    RetrieveResponse,
)
from smartcs.shared.orm_models import KbDocStatus, KbDocument, KbSourceType

logger = logging.getLogger(__name__)

router = APIRouter(tags=["bot"])

# Redis 队列与响应键
CHAT_QUEUE_KEY = "smartcs:chat:queue"
RESPONSE_KEY_PREFIX = "smartcs:response"
RESPONSE_TTL = 120

# 支持的文件扩展名 → KbSourceType 映射
_EXT_TO_SOURCE: dict[str, str] = {
    ".pdf": "PDF",
    ".docx": "DOCX",
    ".html": "HTML",
    ".md": "MARKDOWN",
    ".txt": "TXT",
    ".xlsx": "XLSX",
}
_ALLOWED_EXTENSIONS = set(_EXT_TO_SOURCE.keys())


@router.get("/health")
async def health_check():
    """机器人服务健康检查"""
    return {"status": "healthy", "service": "bot"}


@router.post("/chat/send", response_model=ChatSendResponse)
async def chat_send(body: ChatSendRequest, req: Request):
    """客户端发送消息接口

    消息进入 Redis 队列，由后台 worker 异步处理。
    客户端通过 GET /api/chat/poll 轮询获取结果。
    """
    from fastapi import HTTPException

    redis_client = getattr(req.app.state, "redis_client", None)
    if redis_client is None:
        raise HTTPException(status_code=503, detail="Redis 未就绪")
    session_id = body.session_id or uuid_module.uuid4().hex
    message_id = uuid_module.uuid4().hex

    queue_msg = {
        "session_id": session_id,
        "message_id": message_id,
        "message": body.message,
        "customer_id": body.customer_id,
        "channel": body.channel.value if body.channel else "web",
    }

    await redis_client.lpush(CHAT_QUEUE_KEY, json.dumps(queue_msg, ensure_ascii=False))

    return ChatSendResponse(
        accepted=True,
        message_id=message_id,
        session_id=session_id,
    )


@router.get("/chat/poll", response_model=PollResponse)
async def chat_poll(
    req: Request,
    session_id: str = Query(...),
    timeout: int = Query(default=25, ge=1, le=60),
):
    """长轮询获取机器人回复

    每 0.5 秒检查一次 Redis 响应键，直到超时后返回空消息。
    """
    redis_client = getattr(req.app.state, "redis_client", None)
    if redis_client is None:
        return PollResponse(has_message=False)
    response_key = f"{RESPONSE_KEY_PREFIX}:{session_id}"

    elapsed = 0.0
    interval = 0.5
    while elapsed < timeout:
        raw = await redis_client.get(response_key)
        if raw:
            await redis_client.delete(response_key)
            data = json.loads(raw)
            return PollResponse(**data)
        await asyncio.sleep(interval)
        elapsed += interval

    return PollResponse(has_message=False)


async def process_chat_queue(app) -> None:
    """后台工作器：从 Redis 队列消费消息，调用 Agent 处理并写回响应

    由 lifespan 启动，BRPOP 阻塞等待消息，处理结果存入 Redis 响应键并设置 TTL。
    """
    redis_client = app.state.redis_client
    agent = app.state.agent

    logger.info("聊天队列工作器已启动")

    try:
        while True:
            result = await redis_client.brpop(CHAT_QUEUE_KEY, timeout=1)
            if result is None:
                continue

            _, raw = result
            try:
                task = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("队列消息 JSON 解析失败: %s", raw[:100])
                continue

            session_id = task.get("session_id", "")
            message = task.get("message", "")

            if not session_id or not message:
                logger.warning("队列消息缺少必填字段: %s", task)
                continue

            try:
                agent_result = await agent.run(session_id, message)

                intent = agent_result.get("intent")
                if intent is not None:
                    primary_intent = intent.primary_intent if hasattr(intent, "primary_intent") else None
                    primary_confidence = intent.primary_confidence if hasattr(intent, "primary_confidence") else 0.0
                else:
                    primary_intent = None
                    primary_confidence = 0.0

                is_transfer = agent_result.get("should_transfer", False)
                transfer_url = ""
                transfer_reason = agent_result.get("transfer_reason", "")

                # 转人工：调用 star-connection 创建会话，获取客户连接信息
                if is_transfer:
                    star_client = getattr(app.state, "star_client", None)
                    if star_client:
                        try:
                            transfer_req = star_client.build_transfer_request(
                                session_id=session_id,
                                customer_id=task.get("customer_id"),
                                transfer_reason=transfer_reason,
                                transfer_summary=agent_result.get("response", ""),
                                intent=str(primary_intent.value) if primary_intent and hasattr(primary_intent, "value") else "",
                                sentiment="neutral",
                            )
                            transfer_resp = await star_client.create_session(transfer_req)
                            # star-connection Java 返回 camelCase: pollUrl, sessionId
                            transfer_url = transfer_resp.get("pollUrl", "") or transfer_resp.get("poll_url", "")
                            if transfer_url:
                                logger.info("转人工会话已创建: bot_session=%s star_session=%s", session_id, transfer_resp.get("sessionId", transfer_resp.get("session_id", "")))
                        except Exception:
                            logger.exception("转人工调用 star-connection 失败")
                            transfer_reason += "（人工客服系统暂不可用）"
                    else:
                        logger.warning("star_client 未初始化，跳过转人工桥接")

                poll_data = {
                    "has_message": True,
                    "reply": agent_result.get("response", "抱歉，我暂时无法处理您的请求。"),
                    "intent": primary_intent,
                    "confidence": primary_confidence,
                    "source": agent_result.get("response_source", "fallback"),
                    "is_transfer": is_transfer,
                    "transfer_url": transfer_url,
                    "transfer_reason": transfer_reason,
                }

                response_key = f"{RESPONSE_KEY_PREFIX}:{session_id}"
                await redis_client.setex(response_key, RESPONSE_TTL, json.dumps(poll_data, default=str))

                logger.info(
                    "消息处理完成: session_id=%s, intent=%s",
                    session_id,
                    primary_intent.value if primary_intent and hasattr(primary_intent, "value") else "unknown",
                )

            except Exception:
                logger.exception("Agent 处理失败: session_id=%s", session_id)
                error_response = {
                    "has_message": True,
                    "reply": "抱歉，系统处理您的请求时出现错误，请稍后再试。",
                    "intent": None,
                    "confidence": 0.0,
                    "source": "fallback",
                    "is_transfer": False,
                    "transfer_url": "",
                    "transfer_reason": "",
                }
                response_key = f"{RESPONSE_KEY_PREFIX}:{session_id}"
                await redis_client.setex(response_key, RESPONSE_TTL, json.dumps(error_response, ensure_ascii=False))

    except asyncio.CancelledError:
        logger.info("聊天队列工作器收到取消信号，正在关闭")
        raise


async def start_chat_worker(app) -> None:
    """启动聊天队列后台工作器（在 lifespan 中调用）"""
    task = asyncio.create_task(process_chat_queue(app))
    app.state._chat_worker_task = task
    logger.info("聊天队列后台工作器已启动")


async def stop_chat_worker(app) -> None:
    """停止聊天队列后台工作器（在 lifespan 中调用）"""
    task = getattr(app.state, "_chat_worker_task", None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    logger.info("聊天队列后台工作器已停止")


@router.post("/kb/retrieve", response_model=RetrieveResponse)
async def kb_retrieve(
    request: RetrieveRequest,
    es_client: ESClientDep,
    milvus_collection: MilvusCollectionDep,
    embedding_breaker: EmbeddingBreakerDep,
    reranker: RerankerProviderDep,
):
    """知识库检索接口

    支持混合检索（BM25 + 向量 + RRF 融合）、BM25 单路、向量单路三种模式，
    可选 Reranker 精排。自动降级：嵌入服务不可用时降级到 BM25 only。
    """
    embedding_provider = embedding_breaker.provider if embedding_breaker.is_available else None
    return await retrieve(
        request=request,
        es_client=es_client,
        milvus_collection=milvus_collection,
        embedding_provider=embedding_provider,
        reranker=reranker,
    )


@router.post("/kb/documents")
async def upload_document(
    db: DbSession,
    es_client: ESClientDep,
    milvus_collection: MilvusCollectionDep,
    minio_client: MinioClientDep,
    embedding_breaker: EmbeddingBreakerDep,
    file: UploadFile = File(...),  # noqa: B008
    category: str = Form(...),
    doc_type: str = Form(...),
    card_type: str | None = Form(None),
    customer_tier: str | None = Form(None),
    security_level: str = Form("internal"),
    version: str = Form("1.0"),
    effective_date: str | None = Form(None),
    expiry_date: str | None = Form(None),
    keywords: str = Form(""),
):
    """文档上传接口

    上传文件到 MinIO，创建知识库文档记录，触发摄入管线。
    支持格式：PDF, DOCX, HTML, MD, TXT, XLSX。
    """
    # 1. 校验文件扩展名
    filename = file.filename or ""
    suffix = ""
    if "." in filename:
        suffix = "." + filename.rsplit(".", 1)[-1].lower()

    if suffix not in _ALLOWED_EXTENSIONS:
        raise DocumentFormatError(f"不支持的文件格式: {suffix}，支持: {', '.join(sorted(_ALLOWED_EXTENSIONS))}")

    source_type_str = _EXT_TO_SOURCE[suffix]

    # 2. 读取文件内容
    content_bytes = await file.read()
    content_hash = hashlib.sha256(content_bytes).hexdigest()
    file_size = len(content_bytes)

    # 3. 上传到 MinIO
    minio_object_key = f"{category}/{filename}"
    if minio_client:
        from io import BytesIO

        await asyncio.to_thread(
            minio_client.put_object,
            minio_client._bucket_name if hasattr(minio_client, "_bucket_name") else "smartcs-docs",
            minio_object_key,
            BytesIO(content_bytes),
            file_size,
            content_type=file.content_type or "application/octet-stream",
        )

    # 4. 创建 KbDocument 记录
    from datetime import date as date_type

    kb_doc = KbDocument(
        title=filename,
        source_type=KbSourceType[source_type_str],
        file_path=minio_object_key,
        file_size=file_size,
        content_hash=content_hash,
        category=category,
        doc_type=doc_type,
        card_type=card_type,
        customer_tier=customer_tier,
        security_level=security_level,
        version=version,
        effective_date=date_type.fromisoformat(effective_date) if effective_date else None,
        expiry_date=date_type.fromisoformat(expiry_date) if expiry_date else None,
        status=KbDocStatus.PENDING,
        created_by="api_upload",
    )
    db.add(kb_doc)
    await db.flush()

    # 5. 触发摄入管线（同步执行，Sprint 2 实验阶段便于即时反馈）
    from smartcs.services.common.ingestion import ingest_document
    from smartcs.shared.models import DocumentMetadata

    doc_metadata = DocumentMetadata(
        doc_id=str(kb_doc.id),
        category=category,
        doc_type=doc_type,
        keywords=[k.strip() for k in keywords.split(",") if k.strip()] if keywords else [],
        card_type=card_type,
        customer_tier=customer_tier,
        effective_date=effective_date,
        expiry_date=expiry_date,
        security_level=security_level,
        version=version,
    )

    embedding_provider = embedding_breaker.provider if embedding_breaker.is_available else None

    # 根据文件类型解码文本内容
    if source_type_str == "MARKDOWN" or source_type_str == "TXT" or source_type_str == "HTML":
        text_content = content_bytes.decode("utf-8")
    else:
        # PDF/DOCX/XLSX: 摄入管线内部处理文件路径
        text_content = minio_object_key

    try:
        final_status = await ingest_document(
            doc_id=kb_doc.id,
            file_path=text_content,
            source_type=KbSourceType[source_type_str],
            metadata=doc_metadata,
            embedding_provider=embedding_provider,
            db_session=db,
            es_client=es_client,
            milvus_collection=milvus_collection,
            kafka_producer=None,  # Kafka 可选
        )
        kb_doc.status = KbDocStatus.COMPLETED if final_status == "COMPLETED" else KbDocStatus.FAILED
    except Exception:
        kb_doc.status = KbDocStatus.FAILED

    await db.flush()

    return {
        "doc_id": str(kb_doc.id),
        "status": kb_doc.status.value,
        "chunk_count": kb_doc.chunk_count,
    }
