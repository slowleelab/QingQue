"""文档摄入管道单元测试

覆盖 Parse / Clean / Chunk 阶段的纯函数逻辑。
"""

from __future__ import annotations

from smartcs.services.common.ingestion import (
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
