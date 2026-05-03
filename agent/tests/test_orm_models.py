"""ORM 模型单元测试

覆盖 kb_document / kb_chunk / kb_ingestion_log 三张表的核心行为：
- 字段默认值
- 外键级联删除
- UUID v7 时序排序
"""

from __future__ import annotations

import time

import pytest
import uuid_utils
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from smartcs.shared.orm_models import (
    Base,
    KbChunk,
    KbDocStatus,
    KbDocument,
    KbEmbedStatus,
    KbIngestionLog,
    KbIngestionStage,
    KbIngestionStatus,
    KbSourceType,
)


@pytest.fixture()
def db_session() -> Session:
    """创建 SQLite 内存数据库会话，每个测试独立"""

    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    engine = create_engine("sqlite:///:memory:", echo=False)
    event.listen(engine, "connect", _set_sqlite_pragma)
    Base.metadata.create_all(engine)
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = test_session_factory()
    yield session
    session.close()
    engine.dispose()


# ── KbDocument ──


def test_kb_document_create(db_session: Session) -> None:
    """验证 KbDocument 默认值：status=PENDING, security_level=internal, version=1.0, created_by=system"""
    doc = KbDocument(
        title="信用卡年费政策",
        source_type=KbSourceType.PDF,
        file_path="/data/docs/annual_fee.pdf",
        category="年费",
        doc_type="政策",
    )
    db_session.add(doc)
    db_session.commit()

    persisted = db_session.get(KbDocument, doc.id)
    assert persisted is not None
    assert persisted.status == KbDocStatus.PENDING
    assert persisted.security_level == "internal"
    assert persisted.version == "1.0"
    assert persisted.created_by == "system"
    assert persisted.is_deleted is False
    assert isinstance(persisted.id, uuid_utils.UUID)
    # UUID v7 使用 uuid_utils 生成，校验 version 字段
    assert persisted.id.version == 7


# ── KbChunk ──


def test_kb_chunk_create(db_session: Session) -> None:
    """验证 KbChunk 默认值：embedding_status=PENDING, es_indexed=False, milvus_indexed=False"""
    doc = KbDocument(
        title="测试文档",
        source_type=KbSourceType.TXT,
        file_path="/data/test.txt",
        category="测试",
        doc_type="FAQ",
    )
    db_session.add(doc)
    db_session.flush()

    chunk = KbChunk(
        document_id=doc.id,
        chunk_index=0,
        content="这是第一段文本内容",
        char_count=9,
    )
    db_session.add(chunk)
    db_session.commit()

    persisted = db_session.get(KbChunk, chunk.id)
    assert persisted is not None
    assert persisted.embedding_status == KbEmbedStatus.PENDING
    assert persisted.es_indexed is False
    assert persisted.milvus_indexed is False
    assert persisted.document_id == doc.id


# ── KbIngestionLog ──


def test_kb_ingestion_log_create(db_session: Session) -> None:
    """验证 KbIngestionLog 创建及关联"""
    doc = KbDocument(
        title="测试文档",
        source_type=KbSourceType.MARKDOWN,
        file_path="/data/test.md",
        category="测试",
        doc_type="知识",
    )
    db_session.add(doc)
    db_session.flush()

    log = KbIngestionLog(
        document_id=doc.id,
        stage=KbIngestionStage.PARSE,
        status=KbIngestionStatus.RUNNING,
    )
    db_session.add(log)
    db_session.commit()

    persisted = db_session.get(KbIngestionLog, log.id)
    assert persisted is not None
    assert persisted.stage == KbIngestionStage.PARSE
    assert persisted.status == KbIngestionStatus.RUNNING
    assert persisted.error_message is None
    assert persisted.duration_ms is None


# ── 级联删除 ──


def test_cascade_delete(db_session: Session) -> None:
    """删除文档后，关联的 chunks 和 logs 应自动删除"""
    doc = KbDocument(
        title="级联删除测试",
        source_type=KbSourceType.DOCX,
        file_path="/data/cascade.docx",
        category="测试",
        doc_type="政策",
    )
    db_session.add(doc)
    db_session.flush()

    chunk = KbChunk(
        document_id=doc.id,
        chunk_index=0,
        content="chunk content",
        char_count=13,
    )
    log = KbIngestionLog(
        document_id=doc.id,
        stage=KbIngestionStage.CHUNK,
        status=KbIngestionStatus.SUCCESS,
    )
    db_session.add_all([chunk, log])
    db_session.commit()

    # 记住 ID 供后续验证
    chunk_id = chunk.id
    log_id = log.id

    # 删除文档
    db_session.delete(doc)
    db_session.commit()

    # 清空会话缓存，确保从数据库重新查询
    db_session.expire_all()

    # 验证文档及其子记录均已删除
    assert db_session.get(KbDocument, doc.id) is None
    assert db_session.get(KbChunk, chunk_id) is None
    assert db_session.get(KbIngestionLog, log_id) is None


# ── UUID v7 时序排序 ──


def test_document_uuid_v7_ordered(db_session: Session) -> None:
    """UUID v7 应保持时序顺序：先创建的文档 ID 小于后创建的"""
    doc1 = KbDocument(
        title="第一个文档",
        source_type=KbSourceType.HTML,
        file_path="/data/first.html",
        category="测试",
        doc_type="FAQ",
    )
    db_session.add(doc1)
    db_session.flush()

    # 确保有微小时间差
    time.sleep(0.001)

    doc2 = KbDocument(
        title="第二个文档",
        source_type=KbSourceType.HTML,
        file_path="/data/second.html",
        category="测试",
        doc_type="FAQ",
    )
    db_session.add(doc2)
    db_session.commit()

    # UUID v7 的时间戳部分保证时序递增
    assert doc1.id < doc2.id

    # 按 ID 排序查询应与创建顺序一致
    rows = db_session.scalars(select(KbDocument).order_by(KbDocument.id)).all()
    assert rows[0].title == "第一个文档"
    assert rows[1].title == "第二个文档"
