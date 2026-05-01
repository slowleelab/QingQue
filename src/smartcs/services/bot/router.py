"""机器人服务 HTTP API 路由"""

from __future__ import annotations

import asyncio
import hashlib

from fastapi import APIRouter, File, Form, UploadFile
from smartcs.shared.exceptions import DocumentFormatError
from smartcs.shared.models import (
    ChatRequest,
    ChatResponse,
    IntentLabel,
    RetrieveRequest,
    RetrieveResponse,
    SessionPhase,
)
from smartcs.shared.orm_models import KbDocStatus, KbDocument, KbSourceType

from smartcs.services.common.deps import (
    AgentDep,
    DbSession,
    EmbeddingBreakerDep,
    ESClientDep,
    MilvusCollectionDep,
    MinioClientDep,
    RerankerProviderDep,
    SessionManagerDep,
)
from smartcs.services.common.retrieval import retrieve

router = APIRouter(tags=["bot"])

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


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, agent: AgentDep, session_manager: SessionManagerDep):
    """机器人聊天接口

    基于 LangGraph Agent 编排：意图分类 → 路由 → RAG/业务/兜底 → 转人工判断 → 回复
    """
    # 获取或创建会话
    session = await session_manager.get_or_create(
        request.session_id,
        customer_id=request.customer_id,
        channel_type=request.channel,
    )

    # 记录用户消息
    from datetime import datetime
    import uuid

    from smartcs.shared.models import DialogueTurn

    user_turn = DialogueTurn(
        turn_id=uuid.uuid4().hex,
        session_id=session.session_id,
        speaker="customer",
        content=request.message,
        timestamp=datetime.now(),
    )
    await session_manager.add_turn(session.session_id, user_turn)

    # 运行 Agent 图
    result = await agent.run(session.session_id, request.message)

    # 构造响应
    intent = result.get("intent")
    is_transfer = result.get("should_transfer", False)
    classify_source = result.get("classify_source", "fallback")

    # 确定回复来源
    source = "fallback"
    if result.get("retrieval_context"):
        source = "rag"
    elif classify_source in ("rule", "llm"):
        source = "rag"

    return ChatResponse(
        session_id=session.session_id,
        reply=result.get("response", "抱歉，我暂时无法处理您的请求。"),
        intent=intent.primary_intent if intent else None,
        confidence=intent.primary_confidence if intent else 0.0,
        source=source,
        is_transfer=is_transfer,
    )


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
    file: UploadFile = File(...),
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
    if source_type_str == "MARKDOWN":
        text_content = content_bytes.decode("utf-8")
    elif source_type_str == "TXT":
        text_content = content_bytes.decode("utf-8")
    elif source_type_str == "HTML":
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
