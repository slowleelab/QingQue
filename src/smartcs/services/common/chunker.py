"""结构感知分块器

基于文档类型感知的分块策略，支持 Parent-Child 分块结构。
替代递归字符分割器处理结构化文档（Markdown/HTML），
实现 FAQ 问答对提取、层级分块、表格保护和列表块保持。

核心流程：
1. 解析 Markdown 结构为 Section 树
2. 按 doc_type 选择分块策略（FAQ / 层级）
3. 注入文档元数据并修正 Parent-Child 索引
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum as PyEnum
from typing import Any

from markdown_it import MarkdownIt

from smartcs.services.common.ingestion import chunk_text

logger = logging.getLogger(__name__)


# ── 数据结构 ──


class ChunkType(str, PyEnum):
    """分块结构类型"""

    FAQ_QA = "faq_qa"
    SECTION = "section"
    TABLE = "table"
    LIST_BLOCK = "list_block"
    PLAIN_TEXT = "plain_text"


@dataclass
class Section:
    """Markdown 文档结构节点"""

    heading_level: int  # 0=文档根, 1=H1, 2=H2, ...
    heading_text: str
    content: str = ""  # 该段下的正文（不含子 heading 内容）
    children: list[Section] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)  # markdown 表格原文
    lists: list[str] = field(default_factory=list)  # 列表块


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
    ):
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


# ══════════════════════════════════════════════════════════════
# Markdown 结构解析
# ══════════════════════════════════════════════════════════════


def _strip_yaml_frontmatter(text: str) -> str:
    """移除 Markdown 文本开头的 YAML frontmatter"""
    return _RE_YAML_FRONTMATTER.sub("", text)


def _heading_level_from_token(token_type: str) -> int | None:
    """从 markdown-it 的 heading_open token 类型提取标题层级

    例如 "h1" -> 1, "h2" -> 2, 未匹配返回 None
    """
    match = re.match(r"h(\d)", token_type)
    if match:
        return int(match.group(1))
    return None


def _parse_markdown_structure(text: str, doc_title: str = "") -> list[Section]:
    """解析 Markdown 文本为 Section 树结构

    使用 markdown-it-py 解析 token 树，构建层级 Section 对象。
    - 跟踪当前标题栈（H1 -> H2 -> H3）
    - 收集正文、表格、列表等块到当前标题下
    - 遇到同级或更高级标题时关闭当前 section 并开始新 section
    - 返回顶层 section 列表（通常为 H2 级别）

    Args:
        text: Markdown 文本
        doc_title: 文档标题（用于根节点）

    Returns:
        顶层 Section 列表
    """
    text = _strip_yaml_frontmatter(text)
    md = MarkdownIt()
    tokens = md.parse(text)

    # 根节点：承载 H1 之前的内容
    root = Section(heading_level=0, heading_text=doc_title)
    # 标题栈：维护当前的层级路径
    heading_stack: list[Section] = [root]

    i = 0
    while i < len(tokens):
        token = tokens[i]

        if token.type == "heading_open":
            level = _heading_level_from_token(token.tag)
            if level is None:
                i += 1
                continue

            # 获取标题文本（下一个 token 是 inline，再下一个是 heading_close）
            heading_text = ""
            if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
                heading_text = tokens[i + 1].content.strip()

            new_section = Section(heading_level=level, heading_text=heading_text)

            # 回溯标题栈：找到合适的父级
            # 弹出所有 >= 当前层级的标题
            while len(heading_stack) > 1 and heading_stack[-1].heading_level >= level:
                heading_stack.pop()

            # 新 section 挂在栈顶 section 的 children 下
            heading_stack[-1].children.append(new_section)
            # 新 section 入栈
            heading_stack.append(new_section)

            # 跳过 heading_inline 和 heading_close
            i += 3
            continue

        # 收集内容 token 到当前栈顶 section
        current = heading_stack[-1]

        if token.type == "table_open":
            # 收集整个 table 块（table_open ... table_close）
            table_parts: list[str] = []
            j = i
            while j < len(tokens) and tokens[j].type != "table_close":
                table_parts.append(tokens[j].content if tokens[j].content else "")
                # 对于 table token，需要从原始渲染中获取 markdown 文本
                j += 1
            # 使用 token 的 markup 重建表格
            # markdown-it 的 table token 不直接保留原始 markdown，需要从 token map 回源文本
            table_md = _reconstruct_table(tokens, i)
            if table_md:
                current.tables.append(table_md)
            # 跳到 table_close 之后
            i = j + 1 if j < len(tokens) else j
            continue

        if token.type == "bullet_list_open" or token.type == "ordered_list_open":
            # 收集列表块
            list_md = _reconstruct_list(tokens, i)
            if list_md:
                current.lists.append(list_md)
            # 跳到 list_close 之后
            j = i
            while j < len(tokens):
                if (token.type == "bullet_list_open" and tokens[j].type == "bullet_list_close") or (
                    token.type == "ordered_list_open" and tokens[j].type == "ordered_list_close"
                ):
                    break
                j += 1
            i = j + 1
            continue

        if token.type == "inline":
            # 段落内联文本
            if token.content.strip():
                if current.content:
                    current.content += "\n"
                current.content += token.content.strip()
            i += 1
            continue

        # 其他块级 token（paragraph_open/close 等）跳过
        i += 1

    # 返回根节点的直接子节点；如果根节点本身有内容，也包含根节点
    result: list[Section] = []
    if root.content.strip() or root.tables or root.lists:
        # 根节点有内容，保留
        result.append(root)
    result.extend(root.children)
    return result


def _reconstruct_table(tokens: list[Any], start_idx: int) -> str:
    """从 markdown-it token 重建 markdown 表格文本

    遍历 table_open 到 table_close 之间的 token，
    从 token.map 映射回源文本的行范围来重建表格。
    """
    # 找到 table_open 对应的源文本行范围
    table_open_token = tokens[start_idx]
    if table_open_token.map is None:
        return ""

    line_start = table_open_token.map[0]

    # 找 table_close 确定结束行
    end_idx = start_idx
    while end_idx < len(tokens):
        if tokens[end_idx].type == "table_close":
            tokens[end_idx].map[1] if tokens[end_idx].map is not None else line_start + 1
            break
        end_idx += 1
    else:
        line_start + 1

    # 从 token 的源文本重建
    # 使用 token 内容重建表格
    rows: list[str] = []
    idx = start_idx
    while idx < len(tokens) and tokens[idx].type != "table_close":
        t = tokens[idx]
        if t.type == "tr_open":
            row_cells: list[str] = []
            idx += 1
            while idx < len(tokens) and tokens[idx].type != "tr_close":
                if tokens[idx].type in ("th_open", "td_open"):
                    idx += 1
                    if idx < len(tokens) and tokens[idx].type == "inline":
                        row_cells.append(tokens[idx].content)
                        idx += 1
                    # 跳过 close
                    while idx < len(tokens) and tokens[idx].type not in ("th_close", "td_close"):
                        idx += 1
                idx += 1
            rows.append("| " + " | ".join(row_cells) + " |")
        idx += 1

    if not rows:
        return ""

    # 构建分隔行
    num_cols = rows[0].count("|") - 1
    separator = "| " + " | ".join(["---"] * num_cols) + " |"

    # 插入分隔行到 header 之后
    result_lines = [rows[0], separator] + rows[1:] if len(rows) >= 1 else rows

    return "\n".join(result_lines)


def _reconstruct_list(tokens: list[Any], start_idx: int) -> str:
    """从 markdown-it token 重建 markdown 列表文本"""
    is_ordered = tokens[start_idx].type == "ordered_list_open"
    items: list[str] = []
    idx = start_idx + 1
    item_counter = 1

    while idx < len(tokens):
        t = tokens[idx]
        if (is_ordered and t.type == "ordered_list_close") or (not is_ordered and t.type == "bullet_list_close"):
            break
        if t.type == "list_item_open":
            # 收集 item 的 inline 内容
            item_parts: list[str] = []
            idx += 1
            while idx < len(tokens) and tokens[idx].type != "list_item_close":
                if tokens[idx].type == "inline":
                    item_parts.append(tokens[idx].content.strip())
                elif tokens[idx].type == "bullet_list_open" or tokens[idx].type == "ordered_list_open":
                    # 嵌套列表，递归处理
                    nested = _reconstruct_list(tokens, idx)
                    if nested:
                        item_parts.append("\n" + nested)
                    # 跳到嵌套列表结束
                    nested_is_ordered = tokens[idx].type == "ordered_list_open"
                    while idx < len(tokens):
                        if (nested_is_ordered and tokens[idx].type == "ordered_list_close") or (
                            not nested_is_ordered and tokens[idx].type == "bullet_list_close"
                        ):
                            break
                        idx += 1
                idx += 1
            prefix = f"{item_counter}. " if is_ordered else "- "
            items.append(prefix + " ".join(item_parts))
            item_counter += 1
        idx += 1

    return "\n".join(items)


# ══════════════════════════════════════════════════════════════
# 表格保护
# ══════════════════════════════════════════════════════════════


def _split_table_rows(table_text: str, max_size: int) -> list[str]:
    """按行拆分过大的表格，保持表头复制到每个分片

    - 如果表格在 max_size 内，原样返回
    - 否则按行拆分（以 | 开头的行）
    - 将表头行（前两行：列名 + 分隔符）复制到每个分片
    - 返回表格分片列表

    Args:
        table_text: Markdown 表格原文
        max_size: 单块最大字符数

    Returns:
        表格分片列表
    """
    if len(table_text) <= max_size:
        return [table_text]

    lines = table_text.split("\n")
    if len(lines) < 2:
        # 无法拆分，强制返回原表
        return [table_text]

    # 表头行：列名行 + 分隔符行
    header_line = lines[0]
    separator_line = lines[1]
    header_block = f"{header_line}\n{separator_line}"
    header_len = len(header_block) + 1  # +1 for newline

    # 如果仅表头就超限，返回整表（无法拆分）
    if header_len >= max_size:
        logger.warning("表格表头超过 max_size，无法拆分，保留整表")
        return [table_text]

    data_lines = lines[2:]
    segments: list[str] = []
    current_lines: list[str] = []

    for line in data_lines:
        # 如果添加此行会超限，且当前已有数据行，则分割
        candidate = header_block + "\n" + "\n".join([*current_lines, line])
        if len(candidate) > max_size and current_lines:
            # 输出当前段
            segments.append(header_block + "\n" + "\n".join(current_lines))
            current_lines = [line]
        else:
            current_lines.append(line)

    # 最后一段
    if current_lines:
        segments.append(header_block + "\n" + "\n".join(current_lines))

    return segments if segments else [table_text]


# ══════════════════════════════════════════════════════════════
# FAQ 分块
# ══════════════════════════════════════════════════════════════


def _chunk_faq(
    sections: list[Section],
    doc_metadata: dict,
    doc_title: str,
    max_size: int,
) -> list[StructuredChunk]:
    """FAQ 文档分块策略

    每个 H2 section 视为一个 Q-A 对：
    - heading_text = 问题
    - content = 答案
    创建一个 StructuredChunk，chunk_type = FAQ_QA。
    如果 Q-A 超过 max_size，拆分答案部分并在每个子块前加上问题标题。
    表格保持在 Q-A 上下文中，不单独拆分。

    Args:
        sections: 解析后的 Section 列表
        doc_metadata: 文档元数据
        doc_title: 文档标题
        max_size: 单块最大字符数

    Returns:
        结构化分块列表
    """
    chunks: list[StructuredChunk] = []

    for section in sections:
        # 构建完整 Q-A 内容
        question = section.heading_text
        # 组装答案：正文 + 表格 + 列表
        answer_parts: list[str] = []
        if section.content.strip():
            answer_parts.append(section.content.strip())
        for table in section.tables:
            answer_parts.append(table)
        for lst in section.lists:
            answer_parts.append(lst)
        # 递归收集子 section 内容
        for child in section.children:
            _collect_section_content(child, answer_parts)

        answer_text = "\n\n".join(answer_parts)
        full_content = f"## {question}\n{answer_text}"

        if len(full_content) <= max_size:
            # 单块可容纳
            chunks.append(
                StructuredChunk(
                    content=full_content,
                    chunk_type=ChunkType.FAQ_QA,
                    heading_path=[doc_title, question],
                    metadata=dict(doc_metadata),
                )
            )
        else:
            # 拆分答案部分，每个子块前加问题标题
            question_header = f"## {question}\n"
            sub_chunks = chunk_text(answer_text, chunk_size=max_size - len(question_header), overlap=0)
            for sub in sub_chunks:
                sub_content = question_header + sub
                chunks.append(
                    StructuredChunk(
                        content=sub_content,
                        chunk_type=ChunkType.FAQ_QA,
                        heading_path=[doc_title, question],
                        metadata=dict(doc_metadata),
                    )
                )

    return chunks


def _collect_section_content(section: Section, parts: list[str]) -> None:
    """递归收集 section 及其子节点的所有内容"""
    if section.heading_text:
        prefix = "#" * section.heading_level
        parts.append(f"{prefix} {section.heading_text}")
    if section.content.strip():
        parts.append(section.content.strip())
    for table in section.tables:
        parts.append(table)
    for lst in section.lists:
        parts.append(lst)
    for child in section.children:
        _collect_section_content(child, parts)


# ══════════════════════════════════════════════════════════════
# 层级分块
# ══════════════════════════════════════════════════════════════


def _build_section_full_content(section: Section) -> str:
    """构建 section 的完整内容文本（含子标题、表格、列表）"""
    parts: list[str] = []
    # 主体标题 + 正文
    heading_prefix = "#" * section.heading_level if section.heading_level > 0 else ""
    if heading_prefix and section.heading_text:
        parts.append(f"{heading_prefix} {section.heading_text}")
    if section.content.strip():
        parts.append(section.content.strip())
    # 表格
    for table in section.tables:
        parts.append(table)
    # 列表
    for lst in section.lists:
        parts.append(lst)
    # 子 section
    for child in section.children:
        parts.append(_build_section_full_content(child))

    return "\n\n".join(parts)


def _chunk_hierarchical(
    sections: list[Section],
    doc_metadata: dict,
    doc_title: str,
    max_size: int,
) -> list[StructuredChunk]:
    """层级分块策略

    对每个 H2 section：
    1. 构建完整内容，如果 <= max_size，创建单个 SECTION 块
    2. 如果 > max_size，创建 parent 块（含标题和子节摘要），然后为每个 H3 子节/表格/列表创建 child 块
    3. 表格保护：每个表格作为独立的 TABLE 块，不被拆分
    4. 列表块：如果大小合适则作为 LIST_BLOCK 整块保留，否则在列表项边界拆分

    Args:
        sections: 解析后的 Section 列表
        doc_metadata: 文档元数据
        doc_title: 文档标题
        max_size: 单块最大字符数

    Returns:
        结构化分块列表
    """
    chunks: list[StructuredChunk] = []

    for section in sections:
        full_content = _build_section_full_content(section)

        if len(full_content) <= max_size:
            # 整个 section 可作为单块
            chunks.append(
                StructuredChunk(
                    content=full_content,
                    chunk_type=ChunkType.SECTION,
                    heading_path=[doc_title, section.heading_text],
                    metadata=dict(doc_metadata),
                )
            )
        else:
            # 需要 Parent-Child 拆分
            _split_section_with_parent_child(section, doc_metadata, doc_title, max_size, chunks)

    return chunks


def _split_section_with_parent_child(
    section: Section,
    doc_metadata: dict,
    doc_title: str,
    max_size: int,
    chunks: list[StructuredChunk],
) -> None:
    """对超大的 section 执行 Parent-Child 拆分

    - Parent 块包含标题和子节摘要
    - Child 块为每个 H3 子节/表格/列表创建独立块
    - 修正 parent_index 和 child_indices
    """
    parent_idx = len(chunks)  # parent 在 chunks 列表中的位置

    # 构建 parent 内容：标题 + 子节概要
    parent_parts: list[str] = []
    heading_prefix = "#" * section.heading_level if section.heading_level > 0 else ""
    if heading_prefix and section.heading_text:
        parent_parts.append(f"{heading_prefix} {section.heading_text}")

    # section 自身的正文（非子 section 内容）
    if section.content.strip():
        parent_parts.append(section.content.strip())

    # 子节概要
    subsection_summaries: list[str] = []
    for child in section.children:
        if child.heading_text:
            subsection_summaries.append(f"- {child.heading_text}")
    for _table in section.tables:
        subsection_summaries.append("- [表格]")
    for _lst in section.lists:
        subsection_summaries.append("- [列表]")

    if subsection_summaries:
        parent_parts.append("子节概要：\n" + "\n".join(subsection_summaries))

    parent_content = "\n\n".join(parent_parts)

    # 先创建 parent 占位（后续可能调整 child_indices）
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

    # 为 section 自身的表格创建 child 块
    for table in section.tables:
        table_segments = _split_table_rows(table, max_size)
        for seg in table_segments:
            child_idx = len(chunks)
            child_indices.append(child_idx)
            chunks.append(
                StructuredChunk(
                    content=seg,
                    chunk_type=ChunkType.TABLE,
                    heading_path=[doc_title, section.heading_text],
                    metadata=dict(doc_metadata),
                    parent_index=parent_idx,
                )
            )

    # 为 section 自身的列表创建 child 块
    for lst in section.lists:
        _add_list_child(lst, doc_metadata, doc_title, section.heading_text, max_size, parent_idx, chunks, child_indices)

    # 为每个子 section 创建 child 块
    for child in section.children:
        _add_subsection_child(child, doc_metadata, doc_title, section.heading_text, max_size, parent_idx, chunks, child_indices)

    # 回写 parent 的 child_indices
    parent_chunk.child_indices = child_indices


def _add_list_child(
    list_text: str,
    doc_metadata: dict,
    doc_title: str,
    parent_heading: str,
    max_size: int,
    parent_idx: int,
    chunks: list[StructuredChunk],
    child_indices: list[int],
) -> None:
    """添加列表 child 块

    如果列表在 max_size 内，作为 LIST_BLOCK 整块保留；
    否则在列表项边界拆分。
    """
    if len(list_text) <= max_size:
        child_idx = len(chunks)
        child_indices.append(child_idx)
        chunks.append(
            StructuredChunk(
                content=list_text,
                chunk_type=ChunkType.LIST_BLOCK,
                heading_path=[doc_title, parent_heading],
                metadata=dict(doc_metadata),
                parent_index=parent_idx,
            )
        )
    else:
        # 按列表项拆分
        items = re.split(r"(?=\n(?:- |\d+\. ))", list_text)
        current_parts: list[str] = []
        for item in items:
            if not item.strip():
                continue
            candidate = "\n".join([*current_parts, item]) if current_parts else item
            if len(candidate) > max_size and current_parts:
                # 输出当前累积
                child_idx = len(chunks)
                child_indices.append(child_idx)
                chunks.append(
                    StructuredChunk(
                        content="\n".join(current_parts),
                        chunk_type=ChunkType.LIST_BLOCK,
                        heading_path=[doc_title, parent_heading],
                        metadata=dict(doc_metadata),
                        parent_index=parent_idx,
                    )
                )
                current_parts = [item]
            else:
                current_parts.append(item)

        if current_parts:
            child_idx = len(chunks)
            child_indices.append(child_idx)
            chunks.append(
                StructuredChunk(
                    content="\n".join(current_parts),
                    chunk_type=ChunkType.LIST_BLOCK,
                    heading_path=[doc_title, parent_heading],
                    metadata=dict(doc_metadata),
                    parent_index=parent_idx,
                )
            )


def _add_subsection_child(
    subsection: Section,
    doc_metadata: dict,
    doc_title: str,
    parent_heading: str,
    max_size: int,
    parent_idx: int,
    chunks: list[StructuredChunk],
    child_indices: list[int],
) -> None:
    """为子 section 创建 child 块

    如果子 section 内容在 max_size 内，创建单个 child 块；
    否则进一步拆分（表格、列表单独建块，正文递归拆分）。
    """
    sub_content = _build_section_full_content(subsection)

    if len(sub_content) <= max_size:
        # 整个子 section 可作为单个 child 块
        child_idx = len(chunks)
        child_indices.append(child_idx)
        chunks.append(
            StructuredChunk(
                content=sub_content,
                chunk_type=ChunkType.SECTION,
                heading_path=[doc_title, parent_heading, subsection.heading_text],
                metadata=dict(doc_metadata),
                parent_index=parent_idx,
            )
        )
    else:
        # 子 section 也需要拆分：将表格、列表、正文分别建块
        sub_parts: list[str] = []
        heading_prefix = "#" * subsection.heading_level if subsection.heading_level > 0 else ""
        if heading_prefix and subsection.heading_text:
            sub_parts.append(f"{heading_prefix} {subsection.heading_text}")
        if subsection.content.strip():
            sub_parts.append(subsection.content.strip())

        # 用子 section 的标题 + 正文作为第一个 child（如果有的话）
        if sub_parts:
            main_text = "\n\n".join(sub_parts)
            if len(main_text) > max_size:
                # 正文部分需要递归字符拆分
                text_chunks = chunk_text(main_text, chunk_size=max_size, overlap=0)
                for tc in text_chunks:
                    idx = len(chunks)
                    child_indices.append(idx)
                    chunks.append(
                        StructuredChunk(
                            content=tc,
                            chunk_type=ChunkType.SECTION,
                            heading_path=[doc_title, parent_heading, subsection.heading_text],
                            metadata=dict(doc_metadata),
                            parent_index=parent_idx,
                        )
                    )
            else:
                # 标题 + 正文可放入单块
                child_idx = len(chunks)
                child_indices.append(child_idx)
                chunks.append(
                    StructuredChunk(
                        content=main_text,
                        chunk_type=ChunkType.SECTION,
                        heading_path=[doc_title, parent_heading, subsection.heading_text],
                        metadata=dict(doc_metadata),
                        parent_index=parent_idx,
                    )
                )
        else:
            # 没有正文，创建最小占位块
            placeholder = f"{heading_prefix} {subsection.heading_text}" if heading_prefix else subsection.heading_text
            child_idx = len(chunks)
            child_indices.append(child_idx)
            chunks.append(
                StructuredChunk(
                    content=placeholder,
                    chunk_type=ChunkType.SECTION,
                    heading_path=[doc_title, parent_heading, subsection.heading_text],
                    metadata=dict(doc_metadata),
                    parent_index=parent_idx,
                )
            )

        # 表格 child
        for table in subsection.tables:
            table_segments = _split_table_rows(table, max_size)
            for seg in table_segments:
                idx = len(chunks)
                child_indices.append(idx)
                chunks.append(
                    StructuredChunk(
                        content=seg,
                        chunk_type=ChunkType.TABLE,
                        heading_path=[doc_title, parent_heading, subsection.heading_text],
                        metadata=dict(doc_metadata),
                        parent_index=parent_idx,
                    )
                )

        # 列表 child
        for lst in subsection.lists:
            _add_list_child(lst, doc_metadata, doc_title, subsection.heading_text, max_size, parent_idx, chunks, child_indices)

        # 更深层子 section
        for deeper in subsection.children:
            _add_subsection_child(deeper, doc_metadata, doc_title, subsection.heading_text, max_size, parent_idx, chunks, child_indices)


# ══════════════════════════════════════════════════════════════
# 回退递归字符分块
# ══════════════════════════════════════════════════════════════


def _fallback_chunk(text: str, doc_metadata: dict, max_size: int, overlap: int) -> list[StructuredChunk]:
    """回退到递归字符分割器

    处理无结构的 TXT、PDF、DOCX 内容。

    Args:
        text: 待分块文本
        doc_metadata: 文档元数据
        max_size: 单块最大字符数
        overlap: 重叠字符数

    Returns:
        结构化分块列表
    """
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


# ══════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════


def chunk_by_structure(
    text: str,
    source_type: str = "MARKDOWN",
    doc_metadata: dict | None = None,
    max_chunk_size: int = 1500,
    overlap: int = 200,
    doc_type: str = "",
) -> list[StructuredChunk]:
    """结构感知分块主入口

    根据文档类型选择分块策略：
    1. 非 MARKDOWN/HTML 类型 -> 递归字符分块回退
    2. MARKDOWN/HTML + doc_type=faq -> FAQ 问答对分块
    3. MARKDOWN/HTML + 其他 -> 层级分块

    Args:
        text: 待分块文本
        source_type: 文档来源类型（MARKDOWN, HTML, PDF, DOCX, TXT 等）
        doc_metadata: 文档元数据字典，注入到每个 chunk 的 metadata 中
        max_chunk_size: 单块最大字符数，默认 1500
        overlap: 重叠字符数，默认 200
        doc_type: 文档业务类型（如 "faq" 触发 FAQ 分块策略）

    Returns:
        结构化分块列表，每个 chunk 包含 content, chunk_type, heading_path, metadata, is_parent, child_indices, parent_index
    """
    metadata = doc_metadata or {}

    # HTML 预处理：提取文本后按 MARKDOWN 逻辑处理
    # 注意：对于 HTML，目前先提取纯文本，后续可增强 HTML 结构解析
    if source_type.upper() == "HTML":
        from smartcs.services.common.ingestion import parse_html

        text = parse_html(text)
        # HTML 提取后为纯文本，回退到递归字符分块
        return _fallback_chunk(text, metadata, max_chunk_size, overlap)

    # 非 MARKDOWN 类型回退
    if source_type.upper() != "MARKDOWN":
        return _fallback_chunk(text, metadata, max_chunk_size, overlap)

    # MARKDOWN 结构化分块
    # 提取文档标题（取第一个 H1，或从 metadata 获取）
    doc_title = metadata.get("title", "")
    if not doc_title:
        # 尝试从文本中提取第一个 H1
        h1_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        if h1_match:
            doc_title = h1_match.group(1).strip()

    # 解析 Markdown 结构
    sections = _parse_markdown_structure(text, doc_title)

    if not sections:
        # 解析无结果，回退
        return _fallback_chunk(text, metadata, max_chunk_size, overlap)

    # 根据 doc_type 选择分块策略
    if doc_type.lower() == "faq":
        chunks = _chunk_faq(sections, metadata, doc_title, max_chunk_size)
    else:
        chunks = _chunk_hierarchical(sections, metadata, doc_title, max_chunk_size)

    # 注入 doc_metadata 到每个 chunk 的 metadata
    for chunk in chunks:
        chunk.metadata.update(metadata)

    # 修正 char_count（确保准确）
    for chunk in chunks:
        chunk.char_count = len(chunk.content)

    logger.info(
        "结构感知分块完成: source_type=%s, doc_type=%s, sections=%d, chunks=%d",
        source_type,
        doc_type,
        len(sections),
        len(chunks),
    )

    return chunks
