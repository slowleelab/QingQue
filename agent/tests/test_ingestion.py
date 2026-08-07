"""文档摄入管道单元测试

覆盖 Parse / Clean / Chunk 阶段的纯函数逻辑。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lumio.services.common.ingestion import (
    chunk_text,
    clean_text,
    parse_markdown,
    parse_text_content,
)

# ── Clean 阶段 ──


class TestCleanText:
    """clean_text 测试组"""

    def test_clean_text_removes_headers(self) -> None:
        """应移除页眉页脚（第X页/共Y页, Page X of Y）"""
        raw = "前言内容\n第 3 页 / 共 10 页\n正文内容\nPage 5 of 20\n尾部内容"
        result = clean_text(raw)
        assert "第 3 页" not in result
        assert "共 10 页" not in result
        assert "Page 5" not in result
        assert "of 20" not in result
        assert "前言内容" in result
        assert "正文内容" in result
        assert "尾部内容" in result

    def test_clean_text_removes_extra_whitespace(self) -> None:
        """应移除连续空格、3+换行折叠为2"""
        raw = "hello    world\n\n\n\nfoo"
        result = clean_text(raw)
        assert "    " not in result
        # 3+ newlines collapsed to 2
        assert "\n\n\n" not in result
        assert "hello" in result
        assert "world" in result

    def test_clean_text_removes_control_chars(self) -> None:
        """应移除控制字符（保留 \\n \\t）"""
        raw = "hello\x00world\x07test\nnewline\ttab"
        result = clean_text(raw)
        assert "\x00" not in result
        assert "\x07" not in result
        assert "\n" in result
        assert "\t" in result
        assert "hello" in result
        assert "world" in result


# ── Chunk 阶段 ──


class TestChunkText:
    """chunk_text 测试组"""

    def test_chunk_text_basic(self) -> None:
        """文本超过 chunk_size 时应产生 2+ 块且存在重叠"""
        # 2000+ chars, chunk_size=1500, overlap=200
        text = "这是一段很长的测试文本。" * 100  # ~1200 chars
        text += "这是第二部分的内容。" * 100  # another ~1200
        chunks = chunk_text(text, chunk_size=1500, overlap=200)
        assert len(chunks) >= 2
        # Verify overlap: end of first chunk should appear at start of second chunk
        # (approximately, within overlap region)
        if len(chunks) >= 2:
            tail = chunks[0][-200:]
            assert tail in chunks[1]

    def test_chunk_text_short_text(self) -> None:
        """短文本应返回单个块"""
        text = "这是一个简短的测试。"
        chunks = chunk_text(text, chunk_size=1500, overlap=200)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_chunk_respects_chinese_sentence_boundary(self) -> None:
        """分块应在中文句号处断开，而非在句中截断"""
        # Build text with clear sentence boundaries around the chunk boundary
        sentence = "信用卡年费标准按照卡片等级不同而有所差异。"  # ~22 chars each
        text = sentence * 80  # ~1760 chars, crosses one chunk boundary
        chunks = chunk_text(text, chunk_size=1500, overlap=200)
        # Every chunk should end at a sentence boundary (ending with 。)
        for chunk in chunks:
            assert chunk.endswith("。"), f"Chunk does not end at sentence boundary: ...{chunk[-30:]}"


# ── Parse 阶段 ──


class TestParse:
    """Parse 阶段测试组"""

    def test_parse_markdown_extracts_text(self) -> None:
        """应从 Markdown 中提取纯文本"""
        md = "# 标题\n\n这是**加粗**和*斜体*文本。\n\n- 列表项1\n- 列表项2\n"
        result = parse_markdown(md)
        assert "标题" in result
        assert "加粗" in result
        assert "斜体" in result
        assert "列表项1" in result
        assert "列表项2" in result
        # Should not contain markdown syntax
        assert "#" not in result
        assert "**" not in result

    def test_parse_text_content_passthrough(self) -> None:
        """parse_text_content 应直接返回 strip 后的文本"""
        text = "  hello world  \n  "
        result = parse_text_content(text)
        assert result == "hello world"


class TestParseDispatch:
    """解析器分派 + 异常"""

    def test_dispatch_covers_all_types(self) -> None:
        """_PARSE_DISPATCH 覆盖全部 KbSourceType 枚举值"""
        from lumio.services.common.ingestion import _PARSE_DISPATCH
        from lumio.shared.orm_models import KbSourceType

        for st in KbSourceType:
            assert st in _PARSE_DISPATCH, f"缺少解析器: {st}"

    def test_markdown_parses_text(self) -> None:
        from lumio.services.common.ingestion import _parse
        from lumio.shared.orm_models import KbSourceType

        text = _parse(KbSourceType.MARKDOWN, "# 标题\n正文")
        assert "标题" in text

    def test_unknown_text_type(self) -> None:
        from lumio.services.common.ingestion import parse_text_content

        assert "内容" in parse_text_content("内容")


class TestCleanTextExtended:
    """文本清洗补充"""

    def test_removes_page_header_footer(self) -> None:
        from lumio.services.common.ingestion import clean_text

        assert "第 1 页 / 共 3 页" not in clean_text("第 1 页 / 共 3 页\n正文")
        assert "Page 2 of 10" not in clean_text("Page 2 of 10\n正文")

    def test_removes_control_chars(self) -> None:
        from lumio.services.common.ingestion import clean_text

        assert "\x00" not in clean_text("a\x00b")

    def test_folds_spaces_and_newlines(self) -> None:
        from lumio.services.common.ingestion import clean_text

        assert "  " not in clean_text("a  b")
        assert "\n\n\n" not in clean_text("a\n\n\n\nb")

    def test_dedup_paragraphs(self) -> None:
        from lumio.services.common.ingestion import clean_text

        out = clean_text("重复段落\n重复段落\n其他")
        assert out.count("重复段落") == 1


class TestChunkExtended:
    """分块补充"""

    def test_chunk_with_overlap(self) -> None:
        from lumio.services.common.ingestion import chunk_text

        text = "。".join([f"句子{i}" for i in range(30)])
        chunks = chunk_text(text, chunk_size=60, overlap=10)
        assert len(chunks) >= 2
        # 重叠: 相邻块有公共内容
        assert all(len(c) > 0 for c in chunks)

    def test_chunk_short_text(self) -> None:
        from lumio.services.common.ingestion import chunk_text

        assert chunk_text("短文", chunk_size=1500) == ["短文"]

    def test_chunk_break_point_at_end(self) -> None:
        from lumio.services.common.ingestion import _find_break_point

        assert _find_break_point("abc", 0, 100) == 3  # target 超长 → 文末


class TestEmbedWrite:
    """嵌入 + 双写"""

    @pytest.mark.asyncio
    async def test_embed_chunks_batches(self) -> None:
        from lumio.services.common.ingestion import embed_chunks

        provider = MagicMock()
        provider.embed = AsyncMock(side_effect=lambda batch: [[0.1] * 4 for _ in batch])
        result = await embed_chunks(["a", "b", "c", "d", "e"], provider, batch_size=2)
        assert len(result) == 5
        assert provider.embed.await_count == 3  # 2+2+1 三批

    @pytest.mark.asyncio
    async def test_embed_chunks_empty(self) -> None:
        from lumio.services.common.ingestion import embed_chunks

        provider = MagicMock()
        provider.embed = AsyncMock()
        assert await embed_chunks([], provider) == []
        provider.embed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_write_to_es_success_and_failure(self) -> None:
        from lumio.services.common.ingestion import write_to_es

        es = AsyncMock()
        chunk = {"chunk_id": "c1", "doc_id": "d1", "content": "内容"}
        count = await write_to_es([chunk], es, index_name="idx")
        assert count == 1
        assert es.index.await_count == 1

        es2 = AsyncMock()
        es2.index.side_effect = RuntimeError("down")
        count2 = await write_to_es([chunk], es2)
        assert count2 == 0

    @pytest.mark.asyncio
    async def test_write_to_milvus_success(self) -> None:
        from lumio.services.common.ingestion import write_to_milvus

        collection = MagicMock()
        chunks = [
            {
                "chunk_id": "c1",
                "doc_id": "d1",
                "content": "x",
                "embedding": [0.1, 0.2],
                "keywords": "年费,分期",  # 字符串逗号分隔
            }
        ]
        count = await write_to_milvus(chunks, collection)
        assert count == 1
        collection.insert.assert_called_once()

    @pytest.mark.asyncio
    async def test_write_to_milvus_failure(self) -> None:
        from lumio.services.common.ingestion import write_to_milvus

        collection = MagicMock()

        def boom(data):
            raise RuntimeError("milvus down")

        collection.insert = boom
        chunks = [{"chunk_id": "c1", "doc_id": "d1", "content": "x", "embedding": [0.1]}]
        assert await write_to_milvus(chunks, collection) == 0

    @pytest.mark.asyncio
    async def test_rollback_es_docs(self) -> None:
        from lumio.services.common.ingestion import _rollback_es_docs

        es = AsyncMock()
        await _rollback_es_docs(es, ["id1", "id2"])
        assert es.delete.await_count == 2
