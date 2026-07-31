"""文本清洗器

移除页眉页脚、控制字符、连续空格，段落级 MD5 去重。
"""

from __future__ import annotations

import hashlib
import re

# 页眉页脚正则
_RE_PAGE_HEADER_FOOTER = re.compile(
    r"(第\s*\d+\s*页\s*/?\s*共\s*\d+\s*页|Page\s+\d+\s+of\s+\d+)",
    re.IGNORECASE,
)

# 控制字符（保留 \n \t \r）
_RE_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# 连续空格
_RE_MULTI_SPACES = re.compile(r" {2,}")

# 3+ 换行
_RE_MULTI_NEWLINES = re.compile(r"\n{3,}")


def clean_text(raw: str) -> str:
    """文本清洗

    - 移除页眉页脚（第X页/共Y页, Page X of Y）
    - 移除控制字符（保留 \\n \\t）
    - 移除连续空格
    - 折叠 3+ 换行为 2
    - 段落级 MD5 去重
    """
    # 1. 移除页眉页脚
    text = _RE_PAGE_HEADER_FOOTER.sub("", raw)
    # 2. 移除控制字符
    text = _RE_CONTROL_CHARS.sub("", text)
    # 3. 移除连续空格
    text = _RE_MULTI_SPACES.sub(" ", text)
    # 4. 折叠 3+ 换行为 2
    text = _RE_MULTI_NEWLINES.sub("\n\n", text)

    # 5. 段落级 MD5 去重
    paragraphs = text.split("\n")
    seen_hashes: set[str] = set()
    deduped: list[str] = []
    for para in paragraphs:
        stripped = para.strip()
        if not stripped:
            deduped.append(para)
            continue
        h = hashlib.md5(stripped.encode()).hexdigest()  # noqa: S324 — 去重用途，非安全哈希
        if h not in seen_hashes:
            seen_hashes.add(h)
            deduped.append(para)

    return "\n".join(deduped).strip()
