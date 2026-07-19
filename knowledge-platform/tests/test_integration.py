"""集成测试 — ETL 管线端到端（Mock 基础设施）

使用 Mock 替代真实 ES/PG/Kafka，验证管线编排逻辑正确性。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pipeline.chunker import StructuredChunk, ChunkType
from app.pipeline.cleaner import clean_text
from app.pipeline.parser import parse_markdown


def test_parse_clean_chunk_pipeline():
    """测试 Parse → Clean → Chunk 管线（无外部依赖）"""
    # 1. Parse
    raw = parse_markdown("""## 常见问题

信用卡年费怎么减免？金卡客户消费满6次可减免次年年费。
""")
    assert "信用卡年费" in raw

    # 2. Clean
    cleaned = clean_text(raw)
    assert len(cleaned) > 0

    # 3. Chunk
    from app.pipeline.chunker import chunk_by_structure

    chunks = chunk_by_structure(cleaned, source_type="MARKDOWN", max_chunk_size=1500)
    assert len(chunks) >= 1
    assert all(isinstance(c, StructuredChunk) for c in chunks)


def test_embedding_serialization_roundtrip():
    """测试嵌入向量序列化/反序列化"""
    from app.pipeline.writer import deserialize_embedding, serialize_embedding

    original = [0.1, 0.2, 0.3, 0.4, 0.5] * 200  # 1000 维
    serialized = serialize_embedding(original)
    assert isinstance(serialized, bytes)
    assert len(serialized) == len(original) * 4  # float32 = 4 bytes

    deserialized = deserialize_embedding(serialized)
    assert len(deserialized) == len(original)
    for a, b in zip(original, deserialized, strict=True):
        assert abs(a - b) < 1e-6  # float32 精度


def test_es_filter_building():
    """测试 ES 过滤器构建"""
    from app.retrieval.engine import build_es_filters

    # 合规过滤
    filters = {
        "approval_status": "PUBLISHED",
        "is_current_version": True,
        "category": "年费",
    }
    clauses = build_es_filters(filters)
    assert len(clauses) == 3
    assert {"term": {"approval_status": "PUBLISHED"}} in clauses
    assert {"term": {"is_current_version": True}} in clauses
    assert {"term": {"category": "年费"}} in clauses


def test_filename_sanitization():
    """测试文件名安全化"""
    from app.utils import sanitize_filename

    assert sanitize_filename("../../../etc/passwd") == "passwd"
    assert sanitize_filename("normal.pdf") == "normal.pdf"
    # os.path.basename 只保留文件名部分
    assert sanitize_filename("path/to/file.pdf") == "file.pdf"
    assert ".." not in sanitize_filename("../malicious..pdf")


def test_date_to_epoch_string_and_date():
    """测试日期转换兼容字符串和 date 对象"""
    from datetime import date

    from app.pipeline.writer import _date_to_epoch

    # 字符串
    assert _date_to_epoch("2024-01-01") > 0
    # date 对象
    assert _date_to_epoch(date(2024, 1, 1)) > 0
    # 字符串和 date 结果一致
    assert _date_to_epoch("2024-01-01") == _date_to_epoch(date(2024, 1, 1))
    # None
    assert _date_to_epoch(None) == 0
    # 无效字符串
    assert _date_to_epoch("invalid") == 0
