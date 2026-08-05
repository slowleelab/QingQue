"""统一的轻量 token 估算工具.

CJK 字符 (~2 chars/token) → 系数 0.55
拉丁字符 (~3.3 chars/token) → 系数 0.3
其他 (数字/符号) → 系数 0.8

替代了原本散落在 bot_agent.py / kv_cache.py / context_compressor.py 的 3 份重复实现.
"""

from __future__ import annotations

import re

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")
_LATIN_RE = re.compile(r"[a-zA-Z]")


def estimate_tokens(text: str, *, base_overhead: int = 0) -> int:
    """轻量 token 估算.

    Args:
        text: 输入文本
        base_overhead: 基础开销 (e.g. role + message 框架 = 4 tokens for OpenAI)

    Returns:
        估算的 token 数, 最小为 1
    """
    if not text:
        return base_overhead or 1

    cjk = len(_CJK_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    other = max(0, len(text) - cjk - latin)

    return max(1, int(cjk * 0.55 + latin * 0.3 + other * 0.8)) + base_overhead


def estimate_messages_tokens(messages: list[dict[str, str]], *, per_msg_overhead: int = 4) -> int:
    """估算一整组 messages 的 token 数 (OpenAI 风格: 每条 message 含 4 token 框架)."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        total += estimate_tokens(content, base_overhead=per_msg_overhead)
    return total
