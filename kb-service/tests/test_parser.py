"""Parser 测试 — Markdown 解析 / 文件类型识别 / dispatch

不依赖 docling/pymupdf 等重型解析库, 覆盖纯函数路径.
"""

from __future__ import annotations

import pytest

# kb.orm 顶层会 import uuid_utils, Python 3.14 暂未发布对应 wheel
pytest.importorskip("uuid_utils", reason="kb-service 要求 Python 3.11 (uuid_utils 在 3.14 无 wheel)")

from kb.orm.kb import KbSourceType  # noqa: E402
from kb.pipeline.parser import (  # noqa: E402
    detect_source_type,
    parse,
    parse_html,
    parse_markdown,
    parse_text_content,
)


class TestParseMarkdown:
    def test_strips_yaml_frontmatter(self):
        content = """---
title: 年费政策
tags: [年费, 金卡]
---

# 年费政策

金卡年费 200 元。
"""
        result = parse_markdown(content)
        assert "title:" not in result
        assert "tags:" not in result
        assert "年费政策" in result
        assert "金卡年费 200 元" in result

    def test_preserves_table_structure(self):
        content = """| 卡种   | 年费  | 减免条件         |
|--------|-------|------------------|
| 普卡   | 100元 | 消费满 3 次       |
| 金卡   | 200元 | 消费满 6 次       |"""
        result = parse_markdown(content)
        # 表格关键内容应被保留
        assert "普卡" in result
        assert "金卡" in result
        assert "200" in result

    def test_preserves_list(self):
        content = """## 减免条件
- 消费满 6 次
- 单笔满 100 元
- 不限商户"""
        result = parse_markdown(content)
        assert "消费满 6 次" in result
        assert "单笔满 100 元" in result
        assert "不限商户" in result

    def test_handles_plain_text(self):
        result = parse_markdown("就是一段普通文本")
        assert "就是一段普通文本" in result

    def test_empty_content(self):
        result = parse_markdown("")
        assert result.strip() == ""


class TestParseHtml:
    def test_removes_script_style(self):
        content = """<html>
<head><style>body { color: red; }</style><script>alert(1)</script></head>
<body><h1>年费政策</h1><p>金卡 200 元</p></body>
</html>"""
        result = parse_html(content)
        assert "alert" not in result
        assert "color: red" not in result
        assert "年费政策" in result
        assert "金卡 200 元" in result

    def test_removes_nav_footer(self):
        content = """<html><body>
<nav>导航链接</nav>
<main><p>正文</p></main>
<footer>版权信息</footer>
</body></html>"""
        result = parse_html(content)
        assert "导航链接" not in result
        assert "版权信息" not in result
        assert "正文" in result


class TestParseTextContent:
    def test_strips_whitespace(self):
        assert parse_text_content("  hello world  \n\n") == "hello world"

    def test_empty(self):
        assert parse_text_content("") == ""


class TestDetectSourceType:
    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("doc.pdf",     KbSourceType.PDF),
            ("doc.PDF",     KbSourceType.PDF),
            ("doc.docx",    KbSourceType.DOCX),
            ("doc.html",    KbSourceType.HTML),
            ("doc.htm",     KbSourceType.HTML),
            ("doc.md",      KbSourceType.MARKDOWN),
            ("doc.markdown", KbSourceType.MARKDOWN),
            ("doc.txt",     KbSourceType.TXT),
            ("doc.xlsx",    KbSourceType.XLSX),
        ],
    )
    def test_supported_extensions(self, filename, expected):
        assert detect_source_type(filename) == expected

    def test_unsupported_extension_raises(self):
        with pytest.raises(ValueError, match="无法识别文件类型"):
            detect_source_type("doc.exe")

    def test_no_extension_raises(self):
        with pytest.raises(ValueError):
            detect_source_type("doc")


class TestParseDispatch:
    def test_dispatches_markdown(self):
        result = parse(KbSourceType.MARKDOWN, "# 标题\n正文")
        assert "标题" in result
        assert "正文" in result

    def test_dispatches_text(self):
        result = parse(KbSourceType.TXT, "纯文本内容")
        assert result == "纯文本内容"

    def test_dispatches_html(self):
        result = parse(KbSourceType.HTML, "<p>段落</p>")
        assert "段落" in result

    def test_string_source_type_accepted(self):
        # 字符串也能用 (kb.ingest.request 消息里就是字符串)
        result = parse("MARKDOWN", "# 标题")
        assert "标题" in result

    def test_unsupported_type_raises(self):
        with pytest.raises(ValueError, match="不支持的文档格式"):
            parse("UNKNOWN", "content")
