"""分块器

递归字符分割器 + 结构感知分块（FAQ / 层级 Parent-Child / 表格保护 / 列表块）。
迁移自 SmartCS chunker.py，保持核心逻辑不变。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from markdown_it import MarkdownIt

logger = logging.getLogger(__name__)

# 中文句子结尾（优先断点）
_SENTENCE_ENDINGS = "。！？；\n"
# 中文短语结尾（次优先断点）
_PHRASE_ENDINGS = "，、：\u201c\u201d）】》"


class ChunkType(StrEnum):
    """分块结构类型"""

    FAQ_QA = "faq_qa"
    SECTION = "section"
    TABLE = "table"
    LIST_BLOCK = "list_block"
    PLAIN_TEXT = "plain_text"


@dataclass
class Section:
    """Markdown 文档结构节点"""

    heading_level: int
    heading_text: str
    content: str = ""
    children: list[Section] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    lists: list[str] = field(default_factory=list)


class StructuredChunk:
    """结构化分块结果"""

    __slots__ = ("char_count", "child_indices", "chunk_type", "content", "heading_path", "is_parent", "metadata", "parent_index")

    def __init__(
        self,
        content: str,
        chunk_type: ChunkType = ChunkType.PLAIN_TEXT,
        heading_path: list[str] | None = None,
        metadata: dict | None = None,
        is_parent: bool = False,
        child_indices: list[int] | None = None,
        parent_index: int | None = None,
    ) -> None:
        self.content = content
        self.chunk_type = chunk_type
        self.heading_path = heading_path or []
        self.metadata = metadata or {}
        self.char_count = len(content)
        self.is_parent = is_parent
        self.child_indices = child_indices or []
        self.parent_index = parent_index


# ── YAML frontmatter 正则 ──
_RE_YAML_FRONTMATTER = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)


def _strip_yaml_frontmatter(text: str) -> str:
    return _RE_YAML_FRONTMATTER.sub("", text)


def _heading_level_from_token(token_type: str) -> int | None:
    match = re.match(r"h(\d)", token_type)
    return int(match.group(1)) if match else None


def _find_break_point(text: str, start: int, chunk_size: int) -> int:
    """在目标位置附近搜索最佳断点"""
    target = start + chunk_size
    if target >= len(text):
        return len(text)

    search_start = max(start, target - 200)
    search_end = min(len(text), target + 200)

    # 优先级 1: 句子结尾
    for i in range(search_end - 1, search_start - 1, -1):
        if text[i] in _SENTENCE_ENDINGS:
            best = i + 1
            if abs(best - target) <= 200:
                return best
            break

    # 优先级 2: 短语结尾
    for i in range(search_end - 1, search_start - 1, -1):
        if text[i] in _PHRASE_ENDINGS:
            best = i + 1
            if abs(best - target) <= 200:
                return best
            break

    # 优先级 3: 空格
    for i in range(search_end - 1, search_start - 1, -1):
        if text[i] == " ":
            best = i + 1
            if abs(best - target) <= 200:
                return best
            break

    return target


def chunk_text(text: str, chunk_size: int = 1500, overlap: int = 200) -> list[str]:
    """递归字符分割器"""
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0

    while start < len(text):
        break_pos = _find_break_point(text, start, chunk_size)
        if break_pos <= start:
            break_pos = len(text)

        chunk = text[start:break_pos]
        if chunk.strip():
            chunks.append(chunk)

        if break_pos >= len(text):
            break
        start = break_pos - overlap
        if start <= 0 and len(chunks) == 1:
            start = break_pos

    return chunks


def _parse_markdown_structure(text: str, doc_title: str = "") -> list[Section]:
    """解析 Markdown 文本为 Section 树"""
    text = _strip_yaml_frontmatter(text)
    md = MarkdownIt()
    tokens = md.parse(text)

    root = Section(heading_level=0, heading_text=doc_title)
    heading_stack: list[Section] = [root]

    i = 0
    while i < len(tokens):
        token = tokens[i]

        if token.type == "heading_open":
            level = _heading_level_from_token(token.tag)
            if level is None:
                i += 1
                continue

            heading_text = ""
            if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
                heading_text = tokens[i + 1].content.strip()

            new_section = Section(heading_level=level, heading_text=heading_text)

            while len(heading_stack) > 1 and heading_stack[-1].heading_level >= level:
                heading_stack.pop()

            heading_stack[-1].children.append(new_section)
            heading_stack.append(new_section)
            i += 3
            continue

        current = heading_stack[-1]

        if token.type == "inline" and token.content.strip():
            if current.content:
                current.content += "\n"
            current.content += token.content.strip()

        i += 1

    result: list[Section] = []
    if root.content.strip() or root.tables or root.lists:
        result.append(root)
    result.extend(root.children)
    return result


def _build_section_full_content(section: Section) -> str:
    """构建 section 完整内容（含子标题）"""
    parts: list[str] = []
    heading_prefix = "#" * section.heading_level if section.heading_level > 0 else ""
    if heading_prefix and section.heading_text:
        parts.append(f"{heading_prefix} {section.heading_text}")
    if section.content.strip():
        parts.append(section.content.strip())
    for child in section.children:
        parts.append(_build_section_full_content(child))
    return "\n\n".join(parts)


def _fallback_chunk(text: str, doc_metadata: dict, max_size: int, overlap: int) -> list[StructuredChunk]:
    """回退到递归字符分块"""
    raw_chunks = chunk_text(text, chunk_size=max_size, overlap=overlap)
    return [
        StructuredChunk(
            content=c,
            chunk_type=ChunkType.PLAIN_TEXT,
            heading_path=[],
            metadata=dict(doc_metadata),
        )
        for c in raw_chunks
    ]


def chunk_by_structure(
    text: str,
    source_type: str = "MARKDOWN",
    doc_metadata: dict | None = None,
    max_chunk_size: int = 1500,
    overlap: int = 200,
    doc_type: str = "",
) -> list[StructuredChunk]:
    """结构感知分块主入口

    1. 非 MARKDOWN → 递归字符分块回退
    2. MARKDOWN + doc_type=faq → FAQ 问答对分块
    3. MARKDOWN + 其他 → 层级分块
    """
    metadata = doc_metadata or {}

    if source_type.upper() != "MARKDOWN":
        return _fallback_chunk(text, metadata, max_chunk_size, overlap)

    # 提取文档标题
    doc_title = metadata.get("title", "")
    if not doc_title:
        h1_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        if h1_match:
            doc_title = h1_match.group(1).strip()

    sections = _parse_markdown_structure(text, doc_title)
    if not sections:
        return _fallback_chunk(text, metadata, max_chunk_size, overlap)

    if doc_type.lower() == "faq":
        chunks = _chunk_faq(sections, metadata, doc_title, max_chunk_size)
    else:
        chunks = _chunk_hierarchical(sections, metadata, doc_title, max_chunk_size)

    for chunk in chunks:
        chunk.metadata.update(metadata)
        chunk.char_count = len(chunk.content)

    logger.info(
        "结构感知分块完成: source_type=%s, doc_type=%s, sections=%d, chunks=%d",
        source_type, doc_type, len(sections), len(chunks),
    )
    return chunks


def _chunk_faq(
    sections: list[Section],
    doc_metadata: dict,
    doc_title: str,
    max_size: int,
) -> list[StructuredChunk]:
    """FAQ 文档分块：每个 H2 section 视为一个 Q-A 对"""
    chunks: list[StructuredChunk] = []

    for section in sections:
        question = section.heading_text
        answer_parts: list[str] = []
        if section.content.strip():
            answer_parts.append(section.content.strip())
        for child in section.children:
            _collect_section_content(child, answer_parts)

        answer_text = "\n\n".join(answer_parts)
        full_content = f"## {question}\n{answer_text}"

        if len(full_content) <= max_size:
            chunks.append(
                StructuredChunk(
                    content=full_content,
                    chunk_type=ChunkType.FAQ_QA,
                    heading_path=[doc_title, question],
                    metadata=dict(doc_metadata),
                )
            )
        else:
            question_header = f"## {question}\n"
            sub_chunks = chunk_text(answer_text, chunk_size=max_size - len(question_header), overlap=0)
            for sub in sub_chunks:
                chunks.append(
                    StructuredChunk(
                        content=question_header + sub,
                        chunk_type=ChunkType.FAQ_QA,
                        heading_path=[doc_title, question],
                        metadata=dict(doc_metadata),
                    )
                )
    return chunks


def _collect_section_content(section: Section, parts: list[str]) -> None:
    """递归收集 section 及子节点内容"""
    if section.heading_text:
        prefix = "#" * section.heading_level
        parts.append(f"{prefix} {section.heading_text}")
    if section.content.strip():
        parts.append(section.content.strip())
    for child in section.children:
        _collect_section_content(child, parts)


def _chunk_hierarchical(
    sections: list[Section],
    doc_metadata: dict,
    doc_title: str,
    max_size: int,
) -> list[StructuredChunk]:
    """层级分块策略"""
    chunks: list[StructuredChunk] = []
    for section in sections:
        full_content = _build_section_full_content(section)
        if len(full_content) <= max_size:
            chunks.append(
                StructuredChunk(
                    content=full_content,
                    chunk_type=ChunkType.SECTION,
                    heading_path=[doc_title, section.heading_text],
                    metadata=dict(doc_metadata),
                )
            )
        else:
            _split_section_with_parent_child(section, doc_metadata, doc_title, max_size, chunks)
    return chunks


def _split_section_with_parent_child(
    section: Section,
    doc_metadata: dict,
    doc_title: str,
    max_size: int,
    chunks: list[StructuredChunk],
) -> None:
    """对超大 section 执行 Parent-Child 拆分"""
    parent_idx = len(chunks)

    parent_parts: list[str] = []
    heading_prefix = "#" * section.heading_level if section.heading_level > 0 else ""
    if heading_prefix and section.heading_text:
        parent_parts.append(f"{heading_prefix} {section.heading_text}")
    if section.content.strip():
        parent_parts.append(section.content.strip())

    subsection_summaries: list[str] = []
    for child in section.children:
        if child.heading_text:
            subsection_summaries.append(f"- {child.heading_text}")
    if subsection_summaries:
        parent_parts.append("子节概要：\n" + "\n".join(subsection_summaries))

    parent_content = "\n\n".join(parent_parts)
    parent_chunk = StructuredChunk(
        content=parent_content,
        chunk_type=ChunkType.SECTION,
        heading_path=[doc_title, section.heading_text],
        metadata=dict(doc_metadata),
        is_parent=True,
        child_indices=[],
        parent_index=None,
    )
    chunks.append(parent_chunk)

    child_indices: list[int] = []
    for child in section.children:
        sub_content = _build_section_full_content(child)
        if len(sub_content) <= max_size:
            idx = len(chunks)
            child_indices.append(idx)
            chunks.append(
                StructuredChunk(
                    content=sub_content,
                    chunk_type=ChunkType.SECTION,
                    heading_path=[doc_title, section.heading_text, child.heading_text],
                    metadata=dict(doc_metadata),
                    parent_index=parent_idx,
                )
            )
        else:
            # 子 section 也超大，递归字符拆分
            text_chunks = chunk_text(sub_content, chunk_size=max_size, overlap=0)
            for tc in text_chunks:
                idx = len(chunks)
                child_indices.append(idx)
                chunks.append(
                    StructuredChunk(
                        content=tc,
                        chunk_type=ChunkType.SECTION,
                        heading_path=[doc_title, section.heading_text, child.heading_text],
                        metadata=dict(doc_metadata),
                        parent_index=parent_idx,
                    )
                )

    parent_chunk.child_indices = child_indices
