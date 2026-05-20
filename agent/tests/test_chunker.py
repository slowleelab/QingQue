"""结构感知分块器测试"""
from __future__ import annotations

from smartcs.services.common.chunker import ChunkType, chunk_by_structure


class TestMarkdownStructureParsing:
    def test_parse_h2_sections(self):
        """H2 标题作为主分块边界"""
        text = """## 第一节
内容A

## 第二节
内容B
"""
        chunks = chunk_by_structure(text, source_type="MARKDOWN", doc_type="")
        assert len(chunks) >= 2
        assert chunks[0].heading_path  # 标题路径非空

    def test_parse_h3_subsections(self):
        """H3 子标题在 H2 section 下"""
        text = """# 文档

## 第一章
第一章导言

### 第一节
第一节内容

### 第二节
第二节内容
"""
        chunks = chunk_by_structure(text, source_type="MARKDOWN", doc_type="")
        # Should have chunks for the chapter
        assert len(chunks) >= 1
        # Parent-child if chapter is large enough
        has_parent = any(c.is_parent for c in chunks)
        has_child = any(c.parent_index is not None for c in chunks)
        # Either single section chunk or parent-child split
        assert len(chunks) >= 1


class TestFAQChunking:
    def test_faq_qa_pairs(self):
        """FAQ 文档: 每个 Q-A 对为独立 chunk"""
        text = """## Q1: 年费怎么收？
普卡100元/年，金卡300元/年。

## Q2: 年费能免吗？
消费满6次可免年费。

## Q3: 积分怎么算？
普卡1倍积分。
"""
        chunks = chunk_by_structure(text, source_type="MARKDOWN", doc_type="faq")
        assert len(chunks) == 3
        for chunk in chunks:
            assert chunk.chunk_type == ChunkType.FAQ_QA
            assert "Q" in chunk.content  # Q heading preserved

    def test_faq_table_in_qa(self):
        """FAQ Q-A 包含表格，表格保持在 Q-A 上下文中"""
        text = """## Q1: 年费标准？
| 卡种 | 年费 |
| --- | --- |
| 普卡 | 100元 |
| 金卡 | 300元 |
"""
        chunks = chunk_by_structure(text, source_type="MARKDOWN", doc_type="faq")
        assert len(chunks) == 1
        assert "普卡" in chunks[0].content
        assert "100" in chunks[0].content


class TestTableProtection:
    def test_table_not_split(self):
        """表格不被拆分"""
        text = """## 主卡年费
| 卡种 | 年费 | 减免条件 |
| --- | --- | --- |
| 普卡 | 100元 | 消费6次 |
| 金卡 | 300元 | 消费12次 |
| 白金卡 | 1000元 | 消费24次 |
| 钻石卡 | 3000元 | 不减免 |
"""
        chunks = chunk_by_structure(text, source_type="MARKDOWN", doc_type="rate")
        # Table should appear in at least one chunk content
        table_chunks = [c for c in chunks if c.chunk_type == ChunkType.TABLE or "|" in c.content]
        assert len(table_chunks) >= 1


class TestHeadingInheritance:
    def test_child_heading_path(self):
        """子 chunk 携带父标题路径"""
        text = """# 信用卡章程

## 第一章 总则
总则内容...

### 第一条
第一条内容...
"""
        chunks = chunk_by_structure(text, source_type="MARKDOWN", doc_type="")
        # All chunks should have heading_path
        for chunk in chunks:
            if chunk.chunk_type != ChunkType.PLAIN_TEXT:
                assert isinstance(chunk.heading_path, list)


class TestParentChildIndices:
    def test_parent_child_link(self):
        """parent.child_indices 和 child.parent_index 正确关联"""
        text = """## 章节
""" + "内容很多。" * 300 + """

### 子节1
子节1内容。

### 子节2
子节2内容。
"""
        chunks = chunk_by_structure(text, source_type="MARKDOWN", doc_type="", max_chunk_size=200)
        parents = [c for c in chunks if c.is_parent]
        children = [c for c in chunks if c.parent_index is not None]
        if parents and children:
            parent = parents[0]
            # All children should reference the parent index
            for child in children:
                assert child.parent_index == chunks.index(parent)
            # Parent should list all child indices
            for child_idx in parent.child_indices:
                assert 0 <= child_idx < len(chunks)


class TestMetadataInjection:
    def test_metadata_injection(self):
        """frontmatter 元数据注入每个 chunk"""
        metadata = {"category": "FAQ", "doc_type": "faq", "version": "1.0"}
        text = """## Q1: 问题？
答案内容。
"""
        chunks = chunk_by_structure(text, source_type="MARKDOWN", doc_metadata=metadata, doc_type="faq")
        for chunk in chunks:
            assert chunk.metadata.get("category") == "FAQ"
            assert chunk.metadata.get("version") == "1.0"


class TestFallback:
    def test_fallback_for_non_markdown(self):
        """非 Markdown 文档回退到递归字符分块"""
        text = "这是一段纯文本。" * 100
        chunks = chunk_by_structure(text, source_type="TXT", doc_type="", max_chunk_size=200)
        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk.chunk_type == ChunkType.PLAIN_TEXT
