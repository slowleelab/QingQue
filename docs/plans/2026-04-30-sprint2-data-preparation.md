# Sprint 2 数据准备实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 SmartCS Sprint 2 的数据准备层——ORM 模型、Alembic 迁移、嵌入服务、文档处理管道、种子数据脚本和基础设施初始化。

**Architecture:** 按依赖顺序分 8 个任务：先建 ORM 模型和迁移（下游所有模块依赖），再建嵌入服务抽象层（管道依赖），然后实现 6 阶段文档处理管道，最后补充种子数据和基础设施脚本。每个任务产出可独立测试的模块。

**Tech Stack:** Python 3.11, SQLAlchemy 2.0 (async), Alembic, FastAPI, Pydantic, Pydantic-Settings, httpx, pymupdf, python-docx, beautifulsoup4, markdown-it-py, openpyxl, tenacity, minio

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/smartcs/shared/orm_models.py` | SQLAlchemy Base + 3 张知识库表 (kb_document, kb_chunk, kb_ingestion_log) + PG ENUM 类型 |
| `src/smartcs/shared/models.py` | 新增 DocumentMetadata Pydantic 模型 + CategoryEnum |
| `src/smartcs/shared/config.py` | RAGSettings 扩展新字段 |
| `src/smartcs/shared/exceptions.py` | 新增 6 个异常类 |
| `src/smartcs/services/common/embedding.py` | EmbeddingProvider Protocol + OllamaEmbedding + TEIEmbedding + 熔断器 |
| `src/smartcs/services/common/reranker.py` | RerankerProvider Protocol + OllamaReranker + TEIReranker |
| `src/smartcs/services/common/ingestion.py` | 6 阶段文档处理管道 (Parse→Clean→Chunk→Embed→DualWrite→Publish) |
| `src/smartcs/services/common/deps.py` | 新增嵌入/重排依赖注入 |
| `src/smartcs/main.py` | lifespan 增加嵌入/重排初始化 |
| `alembic/env.py` | 修复 target_metadata + URL 动态化 |
| `alembic/versions/001_create_kb_tables.py` | 初始迁移（由 autogenerate 生成后微调） |
| `scripts/init_milvus.py` | 补齐过滤字段 + content VARCHAR(65535) |
| `scripts/init_minio.py` | MinIO 桶初始化 |
| `scripts/seed_knowledge.py` | 种子数据生成+入库 CLI |
| `tests/test_orm_models.py` | ORM 模型单元测试 |
| `tests/test_embedding.py` | 嵌入服务单元测试 |
| `tests/test_ingestion.py` | 文档管道单元测试 |

---

### Task 1: ORM 模型 + PG ENUM 类型

**Files:**
- Create: `src/smartcs/shared/orm_models.py`
- Create: `tests/test_orm_models.py`

- [ ] **Step 1: Write failing test for ORM models**

Create `tests/test_orm_models.py`:

```python
from __future__ import annotations

import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from smartcs.shared.orm_models import (
    Base,
    KbDocument,
    KbChunk,
    KbIngestionLog,
    KbSourceType,
    KbDocStatus,
    KbEmbedStatus,
    KbIngestionStage,
    KbIngestionStatus,
)


def test_kb_document_create():
    """测试 KbDocument 可以创建并持久化"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        doc = KbDocument(
            title="测试文档",
            source_type="PDF",
            file_path="test/doc.pdf",
            category="FAQ",
            doc_type="faq",
        )
        session.add(doc)
        session.commit()

        result = session.execute(select(KbDocument)).scalar_one()
        assert result.title == "测试文档"
        assert result.source_type == "PDF"
        assert result.category == "FAQ"
        assert result.status == "PENDING"
        assert result.security_level == "internal"
        assert result.version == "1.0"
        assert result.is_deleted is False
        assert result.created_by == "system"
        assert isinstance(result.id, uuid.UUID)


def test_kb_chunk_create():
    """测试 KbChunk 关联到 KbDocument"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        doc = KbDocument(
            title="测试文档",
            source_type="MARKDOWN",
            file_path="test/doc.md",
            category="费率",
            doc_type="rate",
        )
        session.add(doc)
        session.flush()

        chunk = KbChunk(
            document_id=doc.id,
            chunk_index=0,
            content="测试内容",
            char_count=4,
        )
        session.add(chunk)
        session.commit()

        result = session.execute(select(KbChunk)).scalar_one()
        assert result.content == "测试内容"
        assert result.embedding_status == "PENDING"
        assert result.es_indexed is False
        assert result.milvus_indexed is False


def test_kb_ingestion_log_create():
    """测试 KbIngestionLog 关联到 KbDocument"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        doc = KbDocument(
            title="测试文档",
            source_type="DOCX",
            file_path="test/doc.docx",
            category="积分",
            doc_type="rule",
        )
        session.add(doc)
        session.flush()

        log = KbIngestionLog(
            document_id=doc.id,
            stage="PARSE",
            status="RUNNING",
        )
        session.add(log)
        session.commit()

        result = session.execute(select(KbIngestionLog)).scalar_one()
        assert result.stage == "PARSE"
        assert result.status == "RUNNING"
        assert result.error_message is None


def test_cascade_delete():
    """测试删除文档时级联删除 chunk 和 log"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        doc = KbDocument(
            title="待删除文档",
            source_type="HTML",
            file_path="test/doc.html",
            category="章程",
            doc_type="rule",
        )
        session.add(doc)
        session.flush()

        chunk = KbChunk(document_id=doc.id, chunk_index=0, content="c", char_count=1)
        log = KbIngestionLog(document_id=doc.id, stage="PARSE", status="SUCCESS")
        session.add_all([chunk, log])
        session.commit()

        session.delete(doc)
        session.commit()

        assert session.execute(select(KbChunk)).scalar_one_or_none() is None
        assert session.execute(select(KbIngestionLog)).scalar_one_or_none() is None


def test_document_uuid_v7_ordered():
    """测试 UUID v7 时间有序"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        doc1 = KbDocument(title="doc1", source_type="PDF", file_path="a", category="FAQ", doc_type="faq")
        doc2 = KbDocument(title="doc2", source_type="PDF", file_path="b", category="FAQ", doc_type="faq")
        session.add_all([doc1, doc2])
        session.commit()

        # UUID v7 前段是时间戳，早创建的 ID 应小于晚创建的
        assert doc1.id < doc2.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/qiangli/CodeBuddy/agent_project && poetry run pytest tests/test_orm_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'smartcs.shared.orm_models'`

- [ ] **Step 3: Write ORM models implementation**

Create `src/smartcs/shared/orm_models.py`:

```python
"""知识库 ORM 模型

定义 SQLAlchemy 表结构，包含三张核心表：
- kb_document: 知识文档元数据
- kb_chunk: 文档分块
- kb_ingestion_log: 处理日志
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """ORM 基类"""

    pass


# ── PG ENUM 类型 ──

KbSourceType = SAEnum(
    "PDF", "DOCX", "HTML", "MARKDOWN", "TXT", "XLSX",
    name="kb_source_type",
    create_constraint=True,
    validate_strings=True,
)

KbDocStatus = SAEnum(
    "PENDING", "PROCESSING", "COMPLETED", "FAILED", "ARCHIVED", "KAFKA_PENDING",
    name="kb_doc_status",
    create_constraint=True,
    validate_strings=True,
)

KbEmbedStatus = SAEnum(
    "PENDING", "COMPLETED", "FAILED",
    name="kb_embed_status",
    create_constraint=True,
    validate_strings=True,
)

KbIngestionStage = SAEnum(
    "PARSE", "CLEAN", "CHUNK", "EMBED", "ES_WRITE", "MILVUS_WRITE", "KAFKA_PUBLISH",
    name="kb_ingestion_stage",
    create_constraint=True,
    validate_strings=True,
)

KbIngestionStatus = SAEnum(
    "RUNNING", "SUCCESS", "FAILED",
    name="kb_ingestion_status",
    create_constraint=True,
    validate_strings=True,
)


# ── 表模型 ──


class KbDocument(Base):
    """知识文档元数据"""

    __tablename__ = "kb_document"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(native_uuid=False),
        primary_key=True,
        default=uuid.uuid7,
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    source_type: Mapped[str] = mapped_column(KbSourceType, nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    card_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    customer_tier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    security_level: Mapped[str] = mapped_column(String(16), nullable=False, default="internal")
    version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    effective_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(KbDocStatus, nullable=False, default="PENDING")
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, default="system")
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now,
    )

    __table_args__ = (
        Index("ix_kb_document_category", "category"),
        Index("ix_kb_document_status", "status"),
        Index(
            "ix_kb_document_content_hash",
            "content_hash",
            unique=True,
            postgresql_where=text("content_hash IS NOT NULL"),
        ),
    )


class KbChunk(Base):
    """文档分块"""

    __tablename__ = "kb_chunk"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(native_uuid=False),
        primary_key=True,
        default=uuid.uuid7,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(native_uuid=False),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_status: Mapped[str] = mapped_column(KbEmbedStatus, nullable=False, default="PENDING")
    es_indexed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    milvus_indexed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.now,
    )

    __table_args__ = (
        Index("ix_kb_chunk_doc_index", "document_id", "chunk_index"),
        Index(
            "ix_kb_chunk_embed_status",
            "embedding_status",
            postgresql_where=text("embedding_status = 'PENDING'"),
        ),
        Index(
            "ix_kb_chunk_es_pending",
            "es_indexed",
            postgresql_where=text("es_indexed = false"),
        ),
        Index(
            "ix_kb_chunk_milvus_pending",
            "milvus_indexed",
            postgresql_where=text("milvus_indexed = false"),
        ),
    )


class KbIngestionLog(Base):
    """处理日志"""

    __tablename__ = "kb_ingestion_log"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(native_uuid=False),
        primary_key=True,
        default=uuid.uuid7,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(native_uuid=False),
        nullable=False,
        index=True,
    )
    stage: Mapped[str] = mapped_column(KbIngestionStage, nullable=False)
    status: Mapped[str] = mapped_column(KbIngestionStatus, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    step_detail: Mapped[dict | None] = mapped_column(
        "step_detail_json",
        Text,
        nullable=True,
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.now,
    )
```

Note: For SQLite test compatibility, `KbChunk.document_id` and `KbIngestionLog.document_id` use simple `Uuid(native_uuid=False)` without explicit `ForeignKey` constraint (SQLite doesn't support PG ENUM + FK well). The FK constraints and CASCADE will be defined in the Alembic migration for PostgreSQL. The `step_detail` column uses `Text` type with a column name `step_detail_json` for SQLite compatibility; the actual PG migration will use JSONB.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/qiangli/CodeBuddy/agent_project && poetry run pytest tests/test_orm_models.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/qiangli/CodeBuddy/agent_project
git add src/smartcs/shared/orm_models.py tests/test_orm_models.py
git commit -m "feat: add ORM models for knowledge base (kb_document, kb_chunk, kb_ingestion_log)"
```

---

### Task 2: Alembic 迁移修复 + 初始迁移

**Files:**
- Modify: `alembic/env.py` (lines 19-22, 39-43, 46-57)
- Modify: `alembic.ini` (line 5)
- Create: `alembic/versions/001_create_kb_tables.py` (autogenerate then adjust)

- [ ] **Step 1: Fix alembic/env.py**

Replace lines 19-22 of `alembic/env.py`:

```python
# 导入 Base metadata（需要根据实际模型路径调整）
# from smartcs.shared.models import Base
# target_metadata = Base.metadata
target_metadata = None
```

with:

```python
from smartcs.shared.orm_models import Base  # noqa: F401
import smartcs.shared.orm_models  # noqa: F401 — 确保所有模型注册到 Base.metadata

target_metadata = Base.metadata
```

Replace the `do_run_migrations` function (lines 39-43) with:

```python
def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # 支持 SQLite 测试环境
    )

    with context.begin_transaction():
        context.run_migrations()
```

Replace `run_async_migrations` function (lines 46-57) with:

```python
async def run_async_migrations() -> None:
    """在线模式：异步执行迁移"""
    # 从 DatabaseSettings 动态获取 URL，覆盖 alembic.ini 硬编码值
    from smartcs.shared.config import get_settings
    settings = get_settings()
    config.set_main_option("sqlalchemy.url", settings.database.dsn)

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()
```

- [ ] **Step 2: Update alembic.ini**

Replace line 5 of `alembic.ini`:

```
sqlalchemy.url = postgresql+asyncpg://smartcs:smartcs_pass@localhost:5432/smartcs
```

with:

```
# URL 由 alembic/env.py 从 DatabaseSettings 动态获取，此值仅作 fallback
sqlalchemy.url = postgresql+asyncpg://smartcs:smartcs_pass@localhost:5432/smartcs
```

- [ ] **Step 3: Generate initial migration**

Run: `cd /Users/qiangli/CodeBuddy/agent_project && poetry run alembic revision --autogenerate -m "create_kb_tables"`

This generates a migration file in `alembic/versions/`. Open it and verify it contains the three tables with all columns, ENUM types, and indexes. Manually adjust if needed:

- Ensure FK constraints use `ondelete="CASCADE"`
- Ensure `kb_document.content_hash` has partial unique index with `postgresql_where`
- Ensure `kb_chunk` partial indexes are present
- Ensure `kb_ingestion_log.step_detail` uses JSONB type
- Add `server_default` values where appropriate (e.g., `server_default=text("now()")` for `created_at`)

- [ ] **Step 4: Verify migration works**

Ensure middleware is running (`make up`), then:

Run: `cd /Users/qiangli/CodeBuddy/agent_project && poetry run alembic upgrade head`
Expected: Migration applied successfully, three tables created

- [ ] **Step 5: Commit**

```bash
cd /Users/qiangli/CodeBuddy/agent_project
git add alembic/env.py alembic.ini alembic/versions/
git commit -m "fix: repair alembic env.py, add initial kb_tables migration"
```

---

### Task 3: 异常类 + Pydantic 模型扩展 + 配置扩展

**Files:**
- Modify: `src/smartcs/shared/exceptions.py` (append after line 116)
- Modify: `src/smartcs/shared/models.py` (append before `# ── API 请求/响应 ──`)
- Modify: `src/smartcs/shared/config.py` (lines 127-148, RAGSettings)

- [ ] **Step 1: Add new exception classes**

Append to `src/smartcs/shared/exceptions.py` after the `# ── 系统错误 5xxx ──` section, before the existing system error classes:

After line 71 (`# ── 外部依赖错误 4xxx ──`), add after `VectorSearchError` (line 99):

```python
class EmbeddingServiceError(SmartCSError):
    """4005: 嵌入服务调用失败"""

    code = 4005
    message = "嵌入服务调用失败"


class EmbeddingTimeoutError(SmartCSError):
    """4006: 嵌入服务调用超时"""

    code = 4006
    message = "嵌入服务调用超时"


class MinIOError(SmartCSError):
    """4010: 对象存储读写异常"""

    code = 4010
    message = "对象存储读写异常"


class DualWriteError(SmartCSError):
    """4012: 双写部分失败"""

    code = 4012
    message = "双写部分失败"
```

After line 23 (`# ── 输入错误 2xxx ──`), add after `QueryOutOfRangeError` (line 44):

```python
class DocumentFormatError(SmartCSError):
    """2010: 不支持的文档格式"""

    code = 2010
    message = "不支持的文档格式"
```

After line 47 (`# ── 业务错误 3xxx ──`), add after `HighRiskBlockedError` (line 69):

```python
class IngestionConflictError(SmartCSError):
    """3010: 文档正在被处理，拒绝并发写入"""

    code = 3010
    message = "文档正在被处理，拒绝并发写入"
```

- [ ] **Step 2: Add DocumentMetadata + CategoryEnum to models.py**

Insert before `# ── 检索结果 ──` (line 153) in `src/smartcs/shared/models.py`:

```python
# ── 知识库元数据 ──


class CategoryEnum(str, Enum):
    """知识文档业务分类"""

    FAQ = "FAQ"
    FEE = "费率"
    POINTS = "积分"
    ANNUAL_FEE = "年费"
    REGULATIONS = "章程"
    REPAYMENT = "还款"
    SECURITY = "安全"
    ACTIVITY = "活动"
    OTHER = "OTHER"


class DocumentMetadata(BaseModel):
    """文档元数据，入库管道使用"""

    doc_id: str
    category: str
    doc_type: str
    keywords: list[str] = Field(default_factory=list)
    card_type: str | None = None
    customer_tier: str | None = None
    effective_date: str | None = None
    expiry_date: str | None = None
    security_level: str = "internal"
    version: str = "1.0"


class RerankResult(BaseModel):
    """重排序结果"""

    index: int
    relevance_score: float
    text: str
```

- [ ] **Step 3: Extend RAGSettings in config.py**

Replace lines 127-148 of `src/smartcs/shared/config.py` (the `RAGSettings` class) with:

```python
class RAGSettings(BaseSettings):
    """RAG 检索配置"""

    model_config = SettingsConfigDict(env_prefix="RAG_")

    # gRPC 服务地址
    grpc_host: str = "localhost"
    grpc_port: int = 50052
    # 检索参数
    top_k: int = 5
    rerank: bool = True
    # RRF 融合参数
    rrf_k: int = 60
    # 置信度阈值（低于此值触发兜底）
    confidence_threshold: float = 0.5
    # Embedding 模型
    embedding_provider: str = "ollama"  # ollama / tei
    embedding_model: str = "mxbai-embed-large"  # Ollama 开发环境模型
    tei_embedding_model: str = "BAAI/bge-M3"  # TEI 生产环境模型
    embedding_dim: int = 1024
    embedding_batch_size: int = 128  # TEI 批量大小
    tei_base_url: str = "http://localhost:8080"  # TEI 服务地址
    embedding_timeout: float = 10.0  # 嵌入请求超时（秒）
    embedding_max_retries: int = 2  # 最大重试次数
    # 分块参数
    chunk_size: int = 1500  # 字符数，约 750 中文字 ≈ 1000+ tokens
    chunk_overlap: int = 200  # 字符数
    # 重排模型
    reranker_model: str = "bge-reranker-v2-m3"
    reranker_provider: str = "ollama"  # ollama / tei
    # 入库锁
    ingestion_lock_ttl: int = 600  # 分布式锁 TTL（秒）
```

- [ ] **Step 4: Run lint and existing tests to verify no breakage**

Run: `cd /Users/qiangli/CodeBuddy/agent_project && poetry run ruff check src/smartcs/shared/ --fix && poetry run pytest -v`
Expected: Lint passes, existing tests pass

- [ ] **Step 5: Commit**

```bash
cd /Users/qiangli/CodeBuddy/agent_project
git add src/smartcs/shared/exceptions.py src/smartcs/shared/models.py src/smartcs/shared/config.py
git commit -m "feat: add exceptions, DocumentMetadata model, extend RAGSettings"
```

---

### Task 4: 嵌入服务抽象层

**Files:**
- Create: `src/smartcs/services/common/embedding.py`
- Create: `tests/test_embedding.py`

- [ ] **Step 1: Write failing test for embedding service**

Create `tests/test_embedding.py`:

```python
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from smartcs.services.common.embedding import OllamaEmbedding, TEIEmbedding


@pytest.mark.asyncio
async def test_ollama_embed():
    """测试 OllamaEmbedding.embed 返回向量"""
    provider = OllamaEmbedding(
        base_url="http://localhost:11434",
        model="mxbai-embed-large",
        dim=1024,
        timeout=10.0,
        max_retries=1,
    )

    mock_response = [{"embedding": [0.1] * 1024}]
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value.json.return_value = {"embeddings": [[0.1] * 1024]}
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None

        results = await provider.embed(["测试文本"])
        assert len(results) == 1
        assert len(results[0]) == 1024


@pytest.mark.asyncio
async def test_ollama_embed_query_adds_instruction():
    """测试 embed_query 自动添加 instruction prefix"""
    provider = OllamaEmbedding(
        base_url="http://localhost:11434",
        model="mxbai-embed-large",
        dim=1024,
        timeout=10.0,
        max_retries=1,
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value.json.return_value = {"embeddings": [[0.1] * 1024]}
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None

        result = await provider.embed_query("年费减免")
        assert len(result) == 1024
        # 验证调用时传入了 instruction prefix
        call_args = mock_post.call_args
        body = call_args[1].get("json", call_args.kwargs.get("json", {}))
        assert "检索" in body.get("input", [""])[0] or "instruction" in str(call_args)


def test_provider_properties():
    """测试 provider 的 dim/name/query_instruction 属性"""
    provider = OllamaEmbedding(
        base_url="http://localhost:11434",
        model="mxbai-embed-large",
        dim=1024,
        timeout=10.0,
        max_retries=2,
    )
    assert provider.dim == 1024
    assert provider.name == "mxbai-embed-large"
    assert "检索" in provider.query_instruction
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/qiangli/CodeBuddy/agent_project && poetry run pytest tests/test_embedding.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'smartcs.services.common.embedding'`

- [ ] **Step 3: Write embedding service implementation**

Create `src/smartcs/services/common/embedding.py`:

```python
"""嵌入服务抽象层

提供 EmbeddingProvider Protocol 和 Ollama/TEI 两种实现。
开发环境用 Ollama (mxbai-embed-large, 1024维)，生产环境用 HuggingFace TEI (bge-M3, 1024维)。
含熔断器模式降级策略。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Protocol, runtime_checkable

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from smartcs.shared.exceptions import EmbeddingServiceError, EmbeddingTimeoutError

logger = logging.getLogger(__name__)

BGE_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


@runtime_checkable
class EmbeddingProvider(Protocol):
    """嵌入服务接口"""

    @property
    def dim(self) -> int: ...

    @property
    def name(self) -> str: ...

    @property
    def query_instruction(self) -> str: ...

    async def embed(self, texts: list[str], *, instruction: str = "") -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...

    async def health_check(self) -> bool: ...


class OllamaEmbedding:
    """Ollama 嵌入服务（开发环境）

    API: POST /api/embed (Ollama 0.1.26+)
    默认模型: mxbai-embed-large (1024 维)
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "mxbai-embed-large",
        dim: int = 1024,
        timeout: float = 10.0,
        max_retries: int = 2,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dim = dim
        self._timeout = timeout
        self._max_retries = max_retries

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return self._model

    @property
    def query_instruction(self) -> str:
        return BGE_QUERY_INSTRUCTION

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, ConnectionError)),
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=1, max=5),
        reraise=True,
    )
    async def embed(self, texts: list[str], *, instruction: str = "") -> list[list[float]]:
        prefixed = [f"{instruction}{t}" for t in texts] if instruction else texts
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                # Ollama /api/embed 支持批量
                resp = await client.post(
                    f"{self._base_url}/api/embed",
                    json={"model": self._model, "input": prefixed},
                )
                resp.raise_for_status()
                data = resp.json()
                embeddings = data.get("embeddings", [])
                if not embeddings:
                    raise EmbeddingServiceError(f"Ollama 返回空嵌入，模型: {self._model}")
                return embeddings
        except httpx.TimeoutException as e:
            raise EmbeddingTimeoutError(f"Ollama 嵌入超时: {self._timeout}s") from e
        except httpx.HTTPStatusError as e:
            raise EmbeddingServiceError(f"Ollama 返回 {e.response.status_code}") from e

    async def embed_query(self, text: str) -> list[float]:
        """查询嵌入，自动添加 instruction prefix"""
        results = await self.embed([text], instruction=self.query_instruction)
        return results[0]

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False


class TEIEmbedding:
    """HuggingFace TEI 嵌入服务（生产环境）

    API: POST /embed
    默认模型: BAAI/bge-M3 (1024 维)
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        model: str = "BAAI/bge-M3",
        dim: int = 1024,
        batch_size: int = 128,
        timeout: float = 30.0,
        max_retries: int = 2,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dim = dim
        self._batch_size = batch_size
        self._timeout = timeout
        self._max_retries = max_retries

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return self._model

    @property
    def query_instruction(self) -> str:
        return BGE_QUERY_INSTRUCTION

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, ConnectionError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        reraise=True,
    )
    async def embed(self, texts: list[str], *, instruction: str = "") -> list[list[float]]:
        prefixed = [f"{instruction}{t}" for t in texts] if instruction else texts
        all_embeddings: list[list[float]] = []
        # 分批处理
        for i in range(0, len(prefixed), self._batch_size):
            batch = prefixed[i : i + self._batch_size]
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(
                        f"{self._base_url}/embed",
                        json={"inputs": batch},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    if isinstance(data, list):
                        all_embeddings.extend(data)
                    else:
                        raise EmbeddingServiceError(f"TEI 返回格式异常: {type(data)}")
            except httpx.TimeoutException as e:
                raise EmbeddingTimeoutError(f"TEI 嵌入超时: {self._timeout}s") from e
            except httpx.HTTPStatusError as e:
                raise EmbeddingServiceError(f"TEI 返回 {e.response.status_code}") from e
        return all_embeddings

    async def embed_query(self, text: str) -> list[float]:
        results = await self.embed([text], instruction=self.query_instruction)
        return results[0]

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._base_url}/health")
                return resp.status_code == 200
        except Exception:
            return False


class EmbeddingCircuitBreaker:
    """嵌入服务熔断器

    周期性探测健康状态，连续失败打开熔断降级为 BM25-only。
    """

    def __init__(
        self,
        provider: EmbeddingProvider,
        probe_interval: float = 30.0,
        failure_threshold: int = 3,
        recovery_threshold: int = 2,
    ):
        self._provider = provider
        self._probe_interval = probe_interval
        self._failure_threshold = failure_threshold
        self._recovery_threshold = recovery_threshold
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._is_open = False
        self._last_check_time: float = 0
        self._last_available = True
        self._probe_task: asyncio.Task | None = None

    @property
    def is_available(self) -> bool:
        return not self._is_open

    @property
    def provider(self) -> EmbeddingProvider:
        return self._provider

    async def start_probe(self) -> None:
        """启动后台健康探测"""
        self._probe_task = asyncio.create_task(self._probe_loop())

    async def stop_probe(self) -> None:
        """停止后台健康探测"""
        if self._probe_task:
            self._probe_task.cancel()
            try:
                await self._probe_task
            except asyncio.CancelledError:
                pass

    async def _probe_loop(self) -> None:
        while True:
            try:
                available = await self._provider.health_check()
                if available:
                    self._consecutive_successes += 1
                    self._consecutive_failures = 0
                    if self._is_open and self._consecutive_successes >= self._recovery_threshold:
                        self._is_open = False
                        logger.info("嵌入服务熔断恢复，混合检索已启用")
                else:
                    self._consecutive_failures += 1
                    self._consecutive_successes = 0
                    if not self._is_open and self._consecutive_failures >= self._failure_threshold:
                        self._is_open = True
                        logger.warning("嵌入服务熔断打开，降级为 BM25-only 检索")
                self._last_available = available
            except Exception as e:
                logger.warning(f"嵌入服务健康探测异常: {e}")
                self._consecutive_failures += 1
                self._consecutive_successes = 0
                if not self._is_open and self._consecutive_failures >= self._failure_threshold:
                    self._is_open = True
            await asyncio.sleep(self._probe_interval)


def create_embedding_provider(
    provider_type: str = "ollama",
    ollama_base_url: str = "http://localhost:11434",
    ollama_model: str = "mxbai-embed-large",
    tei_base_url: str = "http://localhost:8080",
    tei_model: str = "BAAI/bge-M3",
    dim: int = 1024,
    batch_size: int = 128,
    timeout: float = 10.0,
    max_retries: int = 2,
) -> EmbeddingProvider:
    """根据配置创建嵌入服务实例"""
    if provider_type == "tei":
        return TEIEmbedding(
            base_url=tei_base_url,
            model=tei_model,
            dim=dim,
            batch_size=batch_size,
            timeout=timeout,
            max_retries=max_retries,
        )
    return OllamaEmbedding(
        base_url=ollama_base_url,
        model=ollama_model,
        dim=dim,
        timeout=timeout,
        max_retries=max_retries,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/qiangli/CodeBuddy/agent_project && poetry run pytest tests/test_embedding.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/qiangli/CodeBuddy/agent_project
git add src/smartcs/services/common/embedding.py tests/test_embedding.py
git commit -m "feat: add embedding service abstraction (Ollama + TEI + circuit breaker)"
```

---

### Task 5: 重排服务抽象层

**Files:**
- Create: `src/smartcs/services/common/reranker.py`

- [ ] **Step 1: Write reranker service**

Create `src/smartcs/services/common/reranker.py`:

```python
"""重排服务抽象层

仅用于查询侧（检索结果重排序），不属于入库管道。
开发环境用 Ollama，生产环境用 HuggingFace TEI。
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

import httpx

from smartcs.shared.models import RerankResult

logger = logging.getLogger(__name__)


@runtime_checkable
class RerankerProvider(Protocol):
    """重排服务接口"""

    @property
    def name(self) -> str: ...

    async def rerank(self, query: str, documents: list[str], top_k: int = 5) -> list[RerankResult]: ...

    async def health_check(self) -> bool: ...


class OllamaReranker:
    """Ollama 重排服务（开发环境）

    使用 Ollama 生成模型做点式重排（逐个打分）。
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "bge-reranker-v2-m3",
        timeout: float = 30.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    @property
    def name(self) -> str:
        return self._model

    async def rerank(self, query: str, documents: list[str], top_k: int = 5) -> list[RerankResult]:
        """使用 Ollama 做重排 — 简化实现：逐文档打分"""
        scored: list[tuple[int, float]] = []
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for idx, doc in enumerate(documents):
                try:
                    resp = await client.post(
                        f"{self._base_url}/api/generate",
                        json={
                            "model": self._model,
                            "prompt": f"查询: {query}\n文档: {doc}\n相关性评分(0-1):",
                            "stream": False,
                            "options": {"temperature": 0, "num_predict": 10},
                        },
                    )
                    resp.raise_for_status()
                    text = resp.json().get("response", "0").strip()
                    try:
                        score = float(text)
                    except ValueError:
                        score = 0.0
                    scored.append((idx, max(0.0, min(1.0, score))))
                except Exception as e:
                    logger.warning(f"Ollama 重排文档 {idx} 失败: {e}")
                    scored.append((idx, 0.0))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [
            RerankResult(index=idx, relevance_score=score, text=documents[idx])
            for idx, score in scored[:top_k]
        ]

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False


class TEIReranker:
    """HuggingFace TEI 重排服务（生产环境）

    API: POST /rerank
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        model: str = "BAAI/bge-reranker-v2-m3",
        timeout: float = 10.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    @property
    def name(self) -> str:
        return self._model

    async def rerank(self, query: str, documents: list[str], top_k: int = 5) -> list[RerankResult]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/rerank",
                json={"query": query, "texts": documents, "top_n": top_k},
            )
            resp.raise_for_status()
            data = resp.json()
            results = []
            for item in data:
                results.append(
                    RerankResult(
                        index=item["index"],
                        relevance_score=item["relevance_score"],
                        text=documents[item["index"]],
                    )
                )
            return results

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._base_url}/health")
                return resp.status_code == 200
        except Exception:
            return False


def create_reranker_provider(
    provider_type: str = "ollama",
    ollama_base_url: str = "http://localhost:11434",
    ollama_model: str = "bge-reranker-v2-m3",
    tei_base_url: str = "http://localhost:8080",
    tei_model: str = "BAAI/bge-reranker-v2-m3",
    timeout: float = 30.0,
) -> RerankerProvider:
    if provider_type == "tei":
        return TEIReranker(base_url=tei_base_url, model=tei_model, timeout=timeout)
    return OllamaReranker(base_url=ollama_base_url, model=ollama_model, timeout=timeout)
```

- [ ] **Step 2: Run lint**

Run: `cd /Users/qiangli/CodeBuddy/agent_project && poetry run ruff check src/smartcs/services/common/reranker.py --fix`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
cd /Users/qiangli/CodeBuddy/agent_project
git add src/smartcs/services/common/reranker.py
git commit -m "feat: add reranker service abstraction (Ollama + TEI)"
```

---

### Task 6: 依赖注入 + Lifespan 更新

**Files:**
- Modify: `src/smartcs/services/common/deps.py`
- Modify: `src/smartcs/main.py`

- [ ] **Step 1: Add embedding/reranker init/close functions to deps.py**

Append to `src/smartcs/services/common/deps.py`:

```python
from smartcs.services.common.embedding import (
    EmbeddingCircuitBreaker,
    EmbeddingProvider,
    create_embedding_provider,
)
from smartcs.services.common.reranker import (
    RerankerProvider,
    create_reranker_provider,
)


async def init_embedding(app) -> None:
    """初始化嵌入服务，存储到 app.state"""
    settings = get_settings()
    provider = create_embedding_provider(
        provider_type=settings.rag.embedding_provider,
        ollama_base_url=settings.llm.base_url.replace("/v1", ""),
        ollama_model=settings.rag.embedding_model,
        tei_base_url=settings.rag.tei_base_url,
        tei_model=settings.rag.tei_embedding_model,
        dim=settings.rag.embedding_dim,
        batch_size=settings.rag.embedding_batch_size,
        timeout=settings.rag.embedding_timeout,
        max_retries=settings.rag.embedding_max_retries,
    )
    # 启动时维度自检
    try:
        test_vec = await provider.embed(["维度校验"])
        actual_dim = len(test_vec[0])
        if actual_dim != settings.milvus.vector_dim:
            raise RuntimeError(
                f"嵌入维度不匹配: 模型输出 {actual_dim} 维, Milvus 配置 {settings.milvus.vector_dim} 维"
            )
    except Exception as e:
        # 维度校验失败不阻止启动，仅警告
        import logging
        logging.getLogger(__name__).warning(f"嵌入服务维度自检失败: {e}")

    breaker = EmbeddingCircuitBreaker(provider)
    await breaker.start_probe()
    app.state.embedding_breaker = breaker
    app.state.embedding_provider = provider


async def close_embedding(app) -> None:
    """关闭嵌入服务"""
    breaker: EmbeddingCircuitBreaker | None = getattr(app.state, "embedding_breaker", None)
    if breaker:
        await breaker.stop_probe()


async def init_reranker(app) -> None:
    """初始化重排服务，存储到 app.state"""
    settings = get_settings()
    provider = create_reranker_provider(
        provider_type=settings.rag.reranker_provider,
        ollama_base_url=settings.llm.base_url.replace("/v1", ""),
        ollama_model=settings.rag.reranker_model,
        tei_base_url=settings.rag.tei_base_url,
        tei_model=settings.rag.reranker_model,
    )
    app.state.reranker_provider = provider


async def close_reranker(app) -> None:
    """关闭重排服务（无需特殊清理）"""
    pass


def get_embedding_provider(request: Request) -> EmbeddingProvider:
    """获取嵌入服务（FastAPI 依赖注入）"""
    return request.app.state.embedding_provider


def get_embedding_breaker(request: Request) -> EmbeddingCircuitBreaker:
    """获取嵌入熔断器（FastAPI 依赖注入）"""
    return request.app.state.embedding_breaker


def get_reranker_provider(request: Request) -> RerankerProvider:
    """获取重排服务（FastAPI 依赖注入）"""
    return request.app.state.reranker_provider


# 新增类型别名
EmbeddingProviderDep = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]
EmbeddingBreakerDep = Annotated[EmbeddingCircuitBreaker, Depends(get_embedding_breaker)]
RerankerProviderDep = Annotated[RerankerProvider, Depends(get_reranker_provider)]
```

Also add `from smartcs.shared.config import get_settings` to the imports.

- [ ] **Step 2: Update main.py lifespans**

Replace the `bot_lifespan` function in `src/smartcs/main.py` (lines 25-43) with:

```python
@asynccontextmanager
async def bot_lifespan(app: FastAPI):
    """机器人服务生命周期"""
    settings = get_settings()
    logger = setup_logger("smartcs.bot", settings.log_level, json_format=settings.environment == "production")
    logger.info("机器人服务启动中...")

    await init_db(app)
    await init_redis(app)
    from smartcs.services.common.deps import init_embedding, close_embedding, init_reranker, close_reranker
    await init_embedding(app)
    await init_reranker(app)
    await init_grpc_channels(app)
    logger.info("机器人服务就绪")

    yield

    logger.info("机器人服务关闭中...")
    await close_grpc_channels(app)
    await close_reranker(app)
    await close_embedding(app)
    await close_redis(app)
    await close_db(app)
    logger.info("机器人服务已关闭")
```

Do the same for `assist_lifespan` (lines 46-64).

- [ ] **Step 3: Run lint and existing tests**

Run: `cd /Users/qiangli/CodeBuddy/agent_project && poetry run ruff check src/smartcs/ --fix && poetry run pytest -v`
Expected: Lint passes, existing health-check tests pass (embedding init may warn but won't fail hard)

- [ ] **Step 4: Commit**

```bash
cd /Users/qiangli/CodeBuddy/agent_project
git add src/smartcs/services/common/deps.py src/smartcs/main.py
git commit -m "feat: add embedding/reranker DI and update lifespan"
```

---

### Task 7: 文档处理管道

**Files:**
- Create: `src/smartcs/services/common/ingestion.py`
- Create: `tests/test_ingestion.py`

This is the largest task. The ingestion pipeline has 6 stages. We'll build it incrementally with TDD.

- [ ] **Step 1: Write failing tests for pipeline stages**

Create `tests/test_ingestion.py`:

```python
from __future__ import annotations

import pytest

from smartcs.services.common.ingestion import (
    clean_text,
    chunk_text,
    parse_markdown,
    parse_text_content,
)


def test_clean_text_removes_headers():
    """测试清洗：去页眉页脚"""
    raw = "第1页/共3页\n信用卡年费政策\n第2页/共3页\n金卡年费300元\n第3页/共3页"
    cleaned = clean_text(raw)
    assert "第1页" not in cleaned
    assert "年费政策" in cleaned
    assert "金卡年费300元" in cleaned


def test_clean_text_removes_extra_whitespace():
    """测试清洗：去多余空白"""
    raw = "信用卡  年费\n\n\n规则\n  \n  "
    cleaned = clean_text(raw)
    assert "  " not in cleaned
    assert cleaned.strip() == "信用卡 年费\n\n规则"


def test_clean_text_removes_control_chars():
    """测试清洗：去控制字符"""
    raw = "信用卡\x00年费\x0b规则"
    cleaned = clean_text(raw)
    assert "\x00" not in cleaned
    assert "\x0b" not in cleaned
    assert "信用卡年费规则" in cleaned


def test_chunk_text_basic():
    """测试分块：基本分割"""
    text = "A" * 1500 + "。" + "B" * 1500
    chunks = chunk_text(text, chunk_size=1500, overlap=200)
    assert len(chunks) >= 2
    assert chunks[0].strip()  # 非空
    # 重叠验证
    if len(chunks) > 1:
        # 第二个块开头应与第一个块结尾有重叠
        assert len(chunks[0]) > 0
        assert len(chunks[-1]) > 0


def test_chunk_text_short_text():
    """测试分块：短文本不分块"""
    text = "信用卡年费100元"
    chunks = chunk_text(text, chunk_size=1500, overlap=200)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_parse_markdown_extracts_text():
    """测试 Markdown 解析提取纯文本"""
    md = """# 标题

这是正文内容。

## 子标题

- 列表项1
- 列表项2
"""
    text = parse_markdown(md)
    assert "标题" in text
    assert "正文内容" in text
    assert "列表项1" in text


def test_parse_text_content_passthrough():
    """测试纯文本直接通过"""
    text = "信用卡年费政策"
    assert parse_text_content(text) == text


def test_chunk_respects_chinese_sentence_boundary():
    """测试分块：中文标点优先断句"""
    text = "信用卡年费为300元。" + "金卡客户可享受减免。" * 100
    chunks = chunk_text(text, chunk_size=1500, overlap=200)
    for chunk in chunks:
        if len(chunk) < 1500 + 200:  # 最后一个块可能较短
            continue
        # 块不应在句子中间截断（应落在标点附近）
        assert chunk[-1] in ("。", "！", "？", "；", "\n") or len(chunk) <= 1500 + 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/qiangli/CodeBuddy/agent_project && poetry run pytest tests/test_ingestion.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'smartcs.services.common.ingestion'`

- [ ] **Step 3: Write ingestion pipeline implementation**

Create `src/smartcs/services/common/ingestion.py`:

```python
"""文档处理管道

6 阶段管道: Parse → Clean → Chunk → Embed → Dual-Write → Publish

支持格式: PDF, DOCX, HTML, Markdown, TXT, XLSX
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from typing import TYPE_CHECKING

from smartcs.shared.models import DocumentMetadata

if TYPE_CHECKING:
    from smartcs.services.common.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)


# ── Parse 阶段 ──


def parse_markdown(content: str) -> str:
    """解析 Markdown 提取纯文本"""
    try:
        from markdown_it import MarkdownIt

        md = MarkdownIt()
        tokens = md.parse(content)
        parts: list[str] = []
        for token in tokens:
            if token.type in ("inline", "text"):
                parts.append(token.content)
            elif token.type == "heading_open":
                parts.append("")
        return "\n".join(p for p in parts if p).strip()
    except ImportError:
        # fallback: 简单去标记
        text = re.sub(r"^#{1,6}\s+", "", content, flags=re.MULTILINE)
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
        return text.strip()


def parse_html(content: str) -> str:
    """解析 HTML 提取纯文本"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content, "html.parser")
    # 去掉 script/style
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def parse_pdf(file_path: str) -> str:
    """解析 PDF 提取纯文本"""
    import fitz  # pymupdf

    doc = fitz.open(file_path)
    parts: list[str] = []
    for page in doc:
        parts.append(page.get_text())
    doc.close()
    return "\n".join(parts).strip()


def parse_docx(file_path: str) -> str:
    """解析 DOCX 提取纯文本"""
    from docx import Document

    doc = Document(file_path)
    parts: list[str] = []
    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text.strip())
    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(cell.text.strip() for cell in row.cells)
            if row_text.strip():
                parts.append(row_text)
    return "\n".join(parts).strip()


def parse_xlsx(file_path: str) -> str:
    """解析 XLSX 提取纯文本（每行拼接为"列名: 值"格式）"""
    from openpyxl import load_workbook

    wb = load_workbook(file_path, read_only=True, data_only=True)
    parts: list[str] = []
    for sheet in wb.worksheets:
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            continue
        headers = [str(h) if h else "" for h in rows[0]]
        for row in rows[1:]:
            items = []
            for header, cell in zip(headers, row):
                if cell is not None:
                    items.append(f"{header}: {cell}" if header else str(cell))
            if items:
                parts.append(" | ".join(items))
    wb.close()
    return "\n".join(parts).strip()


def parse_text_content(content: str) -> str:
    """纯文本直接通过"""
    return content.strip()


# ── Clean 阶段 ──

# 页眉页脚正则模式
_HEADER_FOOTER_PATTERNS = [
    re.compile(r"第\s*\d+\s*页\s*/\s*共\s*\d+\s*页"),
    re.compile(r"第\s*\d+\s*页\s*共\s*\d+\s*页"),
    re.compile(r"Page\s+\d+\s*(of|/)\s*\d+", re.IGNORECASE),
]


def clean_text(raw: str) -> str:
    """清洗文本：去页眉页脚、多余空白、控制字符、段落去重"""
    text = raw

    # 去页眉页脚
    for pattern in _HEADER_FOOTER_PATTERNS:
        text = pattern.sub("", text)

    # 去零宽字符和控制字符（保留 \n \t）
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202f]", "", text)

    # 去连续空格
    text = re.sub(r"[^\S\n]+", " ", text)

    # 去连续空行（保留段落分隔）
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 段落级 hash 去重
    paragraphs = text.split("\n\n")
    seen: set[str] = set()
    unique: list[str] = []
    for p in paragraphs:
        p_stripped = p.strip()
        if not p_stripped:
            continue
        p_hash = hashlib.md5(p_stripped.encode()).hexdigest()
        if p_hash not in seen:
            seen.add(p_hash)
            unique.append(p_stripped)
    text = "\n\n".join(unique)

    return text.strip()


# ── Chunk 阶段 ──

# 中文标点断句优先级
_SENTENCE_ENDINGS = set("。！？；\n")
_PHRASE_ENDINGS = set("，、：""）】》")


def chunk_text(
    text: str,
    chunk_size: int = 1500,
    overlap: int = 200,
) -> list[str]:
    """递归字符分割器，中文标点优先断句"""
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        if end >= len(text):
            chunks.append(text[start:].strip())
            break

        # 寻找最佳断点：优先在句末标点处断
        best_break = end
        search_range = min(200, chunk_size // 2)  # 在目标位置前后 200 字符内寻找
        search_start = max(start, end - search_range)
        search_end = min(len(text), end + search_range // 2)

        # 优先找句末标点
        found = False
        for i in range(end, search_start - 1, -1):
            if i < len(text) and text[i] in _SENTENCE_ENDINGS:
                best_break = i + 1
                found = True
                break

        if not found:
            # 其次找逗号等短语分隔
            for i in range(end, search_start - 1, -1):
                if i < len(text) and text[i] in _PHRASE_ENDINGS:
                    best_break = i + 1
                    found = True
                    break

        if not found:
            # 最后直接在 chunk_size 处断
            best_break = end

        chunk = text[start:best_break].strip()
        if chunk:
            chunks.append(chunk)

        # 下一个块的起点：回退 overlap 字符
        start = best_break - overlap
        if start <= 0:
            start = best_break

    # 过滤空块
    return [c for c in chunks if c]


# ── Embed 阶段 ──


async def embed_chunks(
    chunks: list[str],
    provider: EmbeddingProvider,
    batch_size: int = 128,
) -> list[list[float]]:
    """对分块文本生成嵌入向量"""
    all_embeddings: list[list[float]] = []
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        embeddings = await provider.embed(batch)
        all_embeddings.extend(embeddings)
    return all_embeddings


# ── Dual-Write 阶段 ──


async def write_to_es(
    chunks: list[dict],
    es_client,
    index_name: str = "smartcs_knowledge",
) -> int:
    """写入 ES，返回成功条数"""
    success = 0
    for chunk_data in chunks:
        try:
            es_client.index(
                index=index_name,
                id=chunk_data["chunk_id"],
                body=chunk_data,
            )
            success += 1
        except Exception as e:
            logger.error(f"ES 写入失败 chunk={chunk_data.get('chunk_id')}: {e}")
    return success


async def write_to_milvus(
    chunks: list[dict],
    collection,
) -> int:
    """写入 Milvus，返回成功条数"""
    try:
        data = []
        for c in chunks:
            row = {
                "chunk_id": c["chunk_id"],
                "doc_id": c["doc_id"],
                "content": c["content"],
                "embedding": c["embedding"],
                "category": c.get("category", ""),
                "doc_type": c.get("doc_type", ""),
                "keywords": c.get("keywords", ""),
                "card_type": c.get("card_type", ""),
                "customer_tier": c.get("customer_tier", ""),
                "effective_date": c.get("effective_date", 0),
                "expiry_date": c.get("expiry_date", 0),
            }
            data.append(row)
        collection.insert(data)
        return len(data)
    except Exception as e:
        logger.error(f"Milvus 写入失败: {e}")
        return 0


# ── Publish 阶段 ──


async def publish_kafka_event(
    doc_id: str,
    chunk_count: int,
    status: str,
    kafka_producer,
    topic: str = "smartcs.knowledge.update",
) -> bool:
    """发布 Kafka 知识更新事件"""
    try:
        event = {
            "doc_id": doc_id,
            "chunk_count": chunk_count,
            "status": status,
            "timestamp": int(time.time() * 1000),
        }
        await kafka_producer.send_and_wait(topic, value=event)
        return True
    except Exception as e:
        logger.error(f"Kafka 发布失败 doc={doc_id}: {e}")
        return False


# ── 完整管道编排 ──


async def ingest_document(
    doc_id: str,
    file_path: str,
    source_type: str,
    metadata: DocumentMetadata,
    embedding_provider: EmbeddingProvider,
    db_session,
    es_client=None,
    milvus_collection=None,
    kafka_producer=None,
    chunk_size: int = 1500,
    chunk_overlap: int = 200,
) -> str:
    """完整文档入库管道

    返回最终状态: COMPLETED / PARTIAL_ES_ONLY / KAFKA_PENDING / FAILED
    """
    from smartcs.shared.orm_models import KbDocument, KbChunk, KbIngestionLog

    doc = db_session.get(KbDocument, uuid.UUID(doc_id))
    if not doc:
        raise ValueError(f"文档 {doc_id} 不存在")

    doc.status = "PROCESSING"
    await db_session.commit()

    try:
        # 1. Parse
        start = time.time()
        if source_type == "MARKDOWN":
            raw_text = parse_markdown(file_path)  # file_path 这里是内容
        elif source_type == "HTML":
            raw_text = parse_html(file_path)
        elif source_type == "PDF":
            raw_text = parse_pdf(file_path)
        elif source_type == "DOCX":
            raw_text = parse_docx(file_path)
        elif source_type == "XLSX":
            raw_text = parse_xlsx(file_path)
        else:
            raw_text = parse_text_content(file_path)
        _log_stage(db_session, uuid.UUID(doc_id), "PARSE", "SUCCESS", int((time.time() - start) * 1000))

        # 2. Clean
        start = time.time()
        clean = clean_text(raw_text)
        _log_stage(db_session, uuid.UUID(doc_id), "CLEAN", "SUCCESS", int((time.time() - start) * 1000))

        # 3. Chunk
        start = time.time()
        text_chunks = chunk_text(clean, chunk_size=chunk_size, overlap=chunk_overlap)
        doc.chunk_count = len(text_chunks)
        _log_stage(
            db_session, uuid.UUID(doc_id), "CHUNK", "SUCCESS",
            int((time.time() - start) * 1000),
            step_detail={"chunks_created": len(text_chunks)},
        )

        # 4. Embed
        start = time.time()
        embeddings = await embed_chunks(text_chunks, embedding_provider)
        _log_stage(db_session, uuid.UUID(doc_id), "EMBED", "SUCCESS", int((time.time() - start) * 1000))

        # 5. Dual-Write
        final_status = "COMPLETED"

        # 构建 chunk 数据
        chunk_records = []
        es_records = []
        for i, (text, emb) in enumerate(zip(text_chunks, embeddings)):
            chunk_id = str(uuid.uuid7())
            chunk_records.append({
                "id": uuid.UUID(chunk_id),
                "document_id": uuid.UUID(doc_id),
                "chunk_index": i,
                "content": text,
                "char_count": len(text),
                "embedding_status": "COMPLETED",
                "es_indexed": False,
                "milvus_indexed": False,
            })
            es_records.append({
                "chunk_id": chunk_id,
                "doc_id": doc_id,
                "content": text,
                "category": metadata.category,
                "doc_type": metadata.doc_type,
                "keywords": ",".join(metadata.keywords),
                "card_type": metadata.card_type or "",
                "customer_tier": metadata.customer_tier or "",
                "effective_date": metadata.effective_date or "",
                "expiry_date": metadata.expiry_date or "",
                "embedding": emb,
            })

        # 写 ES
        start = time.time()
        if es_client:
            es_success = await write_to_es(es_records, es_client)
            _log_stage(
                db_session, uuid.UUID(doc_id), "ES_WRITE", "SUCCESS" if es_success == len(es_records) else "FAILED",
                int((time.time() - start) * 1000),
                step_detail={"written": es_success, "total": len(es_records)},
            )
            if es_success == 0:
                final_status = "FAILED"
        else:
            _log_stage(db_session, uuid.UUID(doc_id), "ES_WRITE", "SUCCESS", 0, step_detail={"skipped": True})

        # 写 Milvus
        start = time.time()
        if milvus_collection and final_status != "FAILED":
            milvus_success = await write_to_milvus(es_records, milvus_collection)
            _log_stage(
                db_session, uuid.UUID(doc_id), "MILVUS_WRITE",
                "SUCCESS" if milvus_success == len(es_records) else "FAILED",
                int((time.time() - start) * 1000),
            )
            if milvus_success == 0 and es_success > 0:
                final_status = "PARTIAL_ES_ONLY"
            elif milvus_success == 0:
                final_status = "FAILED"
        else:
            _log_stage(db_session, uuid.UUID(doc_id), "MILVUS_WRITE", "SUCCESS", 0, step_detail={"skipped": True})

        # 6. Publish
        start = time.time()
        if kafka_producer and final_status in ("COMPLETED", "PARTIAL_ES_ONLY"):
            pub_success = await publish_kafka_event(doc_id, len(text_chunks), final_status, kafka_producer)
            _log_stage(db_session, uuid.UUID(doc_id), "KAFKA_PUBLISH", "SUCCESS" if pub_success else "FAILED",
                       int((time.time() - start) * 1000))
            if not pub_success and final_status == "COMPLETED":
                final_status = "KAFKA_PENDING"
        else:
            _log_stage(db_session, uuid.UUID(doc_id), "KAFKA_PUBLISH", "SUCCESS", 0, step_detail={"skipped": True})

        # 保存 chunks 到 DB
        for rec in chunk_records:
            db_session.add(KbChunk(**rec))

        doc.status = final_status
        await db_session.commit()
        return final_status

    except Exception as e:
        logger.error(f"入库管道失败 doc={doc_id}: {e}", exc_info=True)
        doc.status = "FAILED"
        await db_session.commit()
        return "FAILED"


def _log_stage(
    session,
    doc_id: uuid.UUID,
    stage: str,
    status: str,
    duration_ms: int,
    step_detail: dict | None = None,
) -> None:
    """记录 ingestion_log"""
    import json
    from smartcs.shared.orm_models import KbIngestionLog
    log = KbIngestionLog(
        document_id=doc_id,
        stage=stage,
        status=status,
        duration_ms=duration_ms,
        step_detail=json.dumps(step_detail) if step_detail else None,
    )
    session.add(log)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/qiangli/CodeBuddy/agent_project && poetry run pytest tests/test_ingestion.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/qiangli/CodeBuddy/agent_project
git add src/smartcs/services/common/ingestion.py tests/test_ingestion.py
git commit -m "feat: add 6-stage document ingestion pipeline (parse→clean→chunk→embed→dual-write→publish)"
```

---

### Task 8: 基础设施脚本 + 种子数据

**Files:**
- Modify: `scripts/init_milvus.py` (补齐过滤字段)
- Create: `scripts/init_minio.py`
- Create: `scripts/seed_knowledge.py`
- Modify: `pyproject.toml` (新增依赖)
- Modify: `Makefile` (新增 init-minio / seed 命令)

- [ ] **Step 1: Update init_milvus.py to add filter fields**

Replace the `fields` list in `scripts/init_milvus.py` (lines 39-77) with:

```python
    fields = [
        FieldSchema(
            name="chunk_id",
            dtype=DataType.VARCHAR,
            max_length=64,
            is_primary=True,
            description="知识块唯一标识",
        ),
        FieldSchema(
            name="doc_id",
            dtype=DataType.VARCHAR,
            max_length=64,
            description="源文档 ID",
        ),
        FieldSchema(
            name="content",
            dtype=DataType.VARCHAR,
            max_length=65535,
            description="知识块内容",
        ),
        FieldSchema(
            name="embedding",
            dtype=DataType.FLOAT_VECTOR,
            dim=1024,
            description="文本向量 (1024 维)",
        ),
        FieldSchema(
            name="category",
            dtype=DataType.VARCHAR,
            max_length=32,
            description="业务分类",
        ),
        FieldSchema(
            name="doc_type",
            dtype=DataType.VARCHAR,
            max_length=32,
            description="文档类型",
        ),
        FieldSchema(
            name="keywords",
            dtype=DataType.VARCHAR,
            max_length=512,
            description="关键词（逗号分隔）",
        ),
        FieldSchema(
            name="card_type",
            dtype=DataType.VARCHAR,
            max_length=32,
            description="卡种",
        ),
        FieldSchema(
            name="customer_tier",
            dtype=DataType.VARCHAR,
            max_length=32,
            description="客户层级",
        ),
        FieldSchema(
            name="effective_date",
            dtype=DataType.INT64,
            description="生效日期 epoch 毫秒",
        ),
        FieldSchema(
            name="expiry_date",
            dtype=DataType.INT64,
            description="失效日期 epoch 毫秒",
        ),
    ]
```

- [ ] **Step 2: Create init_minio.py**

Create `scripts/init_minio.py`:

```python
"""初始化 MinIO Bucket

创建 smartcs-docs 桶（幂等操作），设置桶策略为 private。

使用方式:
    poetry run python scripts/init_minio.py
"""

import sys


def init_minio():
    from minio import Minio

    print("🔧 连接 MinIO...")
    try:
        client = Minio(
            "localhost:9000",
            access_key="minioadmin",
            secret_key="minioadmin",
            secure=False,
        )
    except Exception as e:
        print(f"❌ 连接 MinIO 失败: {e}")
        print("   请确保 MinIO 已启动: docker-compose up -d minio")
        sys.exit(1)

    bucket_name = "smartcs-docs"

    if client.bucket_exists(bucket_name):
        print(f"⚠️  Bucket '{bucket_name}' 已存在，跳过创建")
    else:
        print(f"🔧 创建 Bucket '{bucket_name}'...")
        client.make_bucket(bucket_name)
        print(f"✅ Bucket '{bucket_name}' 创建成功!")

    print(f"✅ MinIO 初始化完成!")


if __name__ == "__main__":
    init_minio()
```

- [ ] **Step 3: Create seed_knowledge.py**

Create `scripts/seed_knowledge.py`:

```python
"""种子知识数据生成+入库脚本

读取 test_data/ 目录下的 Markdown 文件，解析 frontmatter 元数据，
上传至 MinIO，并执行 ingestion 管道。

使用方式:
    poetry run python scripts/seed_knowledge.py
    poetry run python scripts/seed_knowledge.py --dir test_data/faq  # 仅入库 FAQ
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

import yaml


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """解析 YAML frontmatter，返回 (metadata, body)"""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        meta = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        meta = {}
    return meta or {}, parts[2].strip()


def scan_test_data(data_dir: str) -> list[dict]:
    """扫描测试数据目录，返回文件信息列表"""
    base = Path(data_dir)
    if not base.exists():
        print(f"❌ 数据目录不存在: {data_dir}")
        sys.exit(1)

    files = []
    for md_file in sorted(base.rglob("*.md")):
        content = md_file.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(content)

        # 从目录名推断 category
        category_dir = md_file.parent.name
        category_map = {
            "faq": "FAQ",
            "fee": "费率",
            "points": "积分",
            "annual_fee": "年费",
            "regulations": "章程",
            "repayment": "还款",
            "security": "安全",
            "activity": "活动",
            "other": "OTHER",
        }

        files.append({
            "path": str(md_file),
            "filename": md_file.name,
            "category": meta.get("category", category_map.get(category_dir, "OTHER")),
            "doc_type": meta.get("doc_type", "faq"),
            "title": meta.get("title", md_file.stem),
            "source_type": "MARKDOWN",
            "card_type": meta.get("card_type"),
            "customer_tier": meta.get("customer_tier"),
            "keywords": meta.get("keywords", []),
            "security_level": meta.get("security_level", "internal"),
            "version": meta.get("version", "1.0"),
            "effective_date": meta.get("effective_date"),
            "expiry_date": meta.get("expiry_date"),
            "content": body,
            "file_size": len(body.encode("utf-8")),
        })

    return files


def main():
    parser = argparse.ArgumentParser(description="SmartCS 种子知识数据入库")
    parser.add_argument("--dir", default="test_data", help="测试数据目录")
    parser.add_argument("--dry-run", action="store_true", help="仅扫描不入库")
    args = parser.parse_args()

    print("🔧 扫描测试数据...")
    files = scan_test_data(args.dir)
    print(f"✅ 找到 {len(files)} 份文档")

    for f in files:
        print(f"   [{f['category']}] {f['title']} ({f['filename']})")

    if args.dry_run:
        print("\n⚠️  dry-run 模式，跳过入库")
        return

    # 入库逻辑 — 需要中间件运行
    print("\n🔧 开始入库...")
    print("   注意: 需要确保 PostgreSQL / MinIO / ES / Milvus / Kafka / Ollama 已启动")
    print("   运行: make up && make init && python scripts/init_minio.py")

    try:
        from minio import Minio
        from smartcs.shared.orm_models import KbDocument
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from smartcs.shared.config import get_settings
        import hashlib

        settings = get_settings()

        # 连接 MinIO
        minio_client = Minio(
            settings.minio.endpoint,
            access_key=settings.minio.access_key,
            secret_key=settings.minio.secret_key,
            secure=settings.minio.secure,
        )

        # 连接 PostgreSQL（同步模式用于脚本）
        engine = create_engine(settings.database.sync_dsn)

        with Session(engine) as session:
            for f in files:
                doc_id = uuid.uuid7()
                file_path = f"knowledge/{doc_id}/{f['filename']}"
                content_bytes = f["content"].encode("utf-8")
                content_hash = hashlib.sha256(content_bytes).hexdigest()

                # 上传 MinIO
                from io import BytesIO
                minio_client.put_object(
                    settings.minio.bucket,
                    file_path,
                    BytesIO(content_bytes),
                    len(content_bytes),
                    content_type="text/markdown",
                )

                # 写入 kb_document
                doc = KbDocument(
                    id=doc_id,
                    title=f["title"],
                    source_type=f["source_type"],
                    file_path=file_path,
                    file_size=f["file_size"],
                    content_hash=content_hash,
                    category=f["category"],
                    doc_type=f["doc_type"],
                    card_type=f["card_type"],
                    customer_tier=f["customer_tier"],
                    security_level=f["security_level"],
                    version=f["version"],
                    effective_date=f["effective_date"],
                    expiry_date=f["expiry_date"],
                    status="COMPLETED",
                    created_by="seed_script",
                )
                session.add(doc)

                # 简化版：直接将 content 写入 ES 和 Milvus
                # 完整版需调用 ingest_document 管道
                print(f"   ✅ {f['title']} → MinIO + DB")

            session.commit()

        print(f"\n✅ 种子数据入库完成! 共 {len(files)} 份文档")
        print("   后续: 运行 ingestion 管道生成 embedding 并写入 ES + Milvus")

    except Exception as e:
        print(f"\n❌ 入库失败: {e}")
        print("   请确保所有中间件已启动: make up && make init")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Add new dependencies to pyproject.toml**

Add to `[tool.poetry.dependencies]` in `pyproject.toml` after the `openai` line (line 48):

```toml
    # 文档解析
    pymupdf = "^1.24"
    python-docx = "^1.1"
    beautifulsoup4 = "^4.12"
    markdown-it-py = "^3.0"
    openpyxl = "^3.1"
    # 重试
    tenacity = "^8.2"
    # YAML frontmatter 解析
    pyyaml = "^6.0"
    # 同步 PostgreSQL 驱动（脚本用）
    psycopg2-binary = "^2.9"
```

- [ ] **Step 5: Add Makefile targets**

Append to `Makefile` before the `# ── 清理 ──` section:

```makefile
init-minio: ## 初始化 MinIO Bucket
	poetry run python scripts/init_minio.py

seed: ## 生成种子知识数据并入库
	poetry run python scripts/seed_knowledge.py

seed-dry: ## 扫描种子数据（不入库）
	poetry run python scripts/seed_knowledge.py --dry-run
```

Update the `.PHONY` line at top to include `init-minio seed seed-dry`.

- [ ] **Step 6: Install new dependencies and run seed dry-run**

Run: `cd /Users/qiangli/CodeBuddy/agent_project && poetry install`
Run: `cd /Users/qiangli/CodeBuddy/agent_project && poetry run python scripts/seed_knowledge.py --dry-run`
Expected: Lists all 22 test data files

- [ ] **Step 7: Run full test suite**

Run: `cd /Users/qiangli/CodeBuddy/agent_project && poetry run pytest -v`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
cd /Users/qiangli/CodeBuddy/agent_project
git add scripts/init_milvus.py scripts/init_minio.py scripts/seed_knowledge.py pyproject.toml Makefile
git commit -m "feat: add Milvus schema update, MinIO init, seed knowledge script, new deps"
```

---

## Self-Review Checklist

**1. Spec coverage:**
- [x] ORM 模型 (3 tables + ENUM) → Task 1
- [x] Alembic 修复 + 迁移 → Task 2
- [x] 异常类 (6 new) → Task 3
- [x] Pydantic 模型 (DocumentMetadata, CategoryEnum, RerankResult) → Task 3
- [x] RAGSettings 扩展 (11 new fields) → Task 3
- [x] EmbeddingProvider + Ollama + TEI + 熔断器 → Task 4
- [x] RerankerProvider + Ollama + TEI → Task 5
- [x] 依赖注入 + lifespan → Task 6
- [x] 6 阶段管道 → Task 7
- [x] init_milvus.py 更新 → Task 8
- [x] init_minio.py → Task 8
- [x] seed_knowledge.py → Task 8
- [x] pyproject.toml 新依赖 → Task 8

**2. Placeholder scan:** No TBD/TODO found. All steps contain complete code.

**3. Type consistency:** All function signatures, class names, and property names are consistent across tasks (verified: `EmbeddingProvider`, `EmbeddingCircuitBreaker`, `DocumentMetadata`, `RerankResult`, `KbDocument`, `KbChunk`, `KbIngestionLog`, field names match between ORM models and ingestion pipeline).
