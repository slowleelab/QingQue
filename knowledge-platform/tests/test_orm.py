"""ORM 模型测试"""

from app.orm.kb import (
    KbSourceType,
    KbDocStatus,
    KbApprovalStatus,
    KbIngestionStage,
    KbChunk,
    KbDocument,
    KbIngestionLog,
)


def test_kb_source_type_values():
    assert KbSourceType.PDF == "PDF"
    assert KbSourceType.MARKDOWN == "MARKDOWN"
    assert KbSourceType.DOCX == "DOCX"


def test_kb_doc_status_values():
    assert KbDocStatus.PENDING == "PENDING"
    assert KbDocStatus.KAFKA_QUEUED == "KAFKA_QUEUED"
    assert KbDocStatus.COMPLETED == "COMPLETED"
    assert KbDocStatus.FAILED == "FAILED"


def test_kb_approval_status_lifecycle():
    """审批状态生命周期"""
    assert KbApprovalStatus.DRAFT == "DRAFT"
    assert KbApprovalStatus.IN_REVIEW == "IN_REVIEW"
    assert KbApprovalStatus.PUBLISHED == "PUBLISHED"
    assert KbApprovalStatus.SUPERSEDED == "SUPERSEDED"


def test_kb_ingestion_stage_has_extract():
    """确认 EXTRACT 阶段存在"""
    assert hasattr(KbIngestionStage, "EXTRACT")
    assert KbIngestionStage.EXTRACT == "EXTRACT"
    assert hasattr(KbIngestionStage, "KAFKA_PUBLISH")
    assert KbIngestionStage.KAFKA_PUBLISH == "KAFKA_PUBLISH"


def test_kb_chunk_has_embedding_fields():
    """确认 KbChunk 有 embedding + model_version 字段"""
    assert hasattr(KbChunk, "embedding")
    assert hasattr(KbChunk, "model_version")
    assert hasattr(KbChunk, "es_indexed")
    # 确认 milvus_indexed 已删除
    assert not hasattr(KbChunk, "milvus_indexed")


def test_kb_document_has_llm_fields():
    """确认 KbDocument 有 LLM 抽取字段"""
    assert hasattr(KbDocument, "llm_summary")
    assert hasattr(KbDocument, "llm_keywords")
    assert hasattr(KbDocument, "llm_entities")
