"""A1: 真正的上下文压缩 (区别于对话摘要)

3 种算法:
- selective: 去除低信息量 token (停用词/重复) — 轻量, 3-5x 压缩
- llmlingua: Microsoft LLMLingua, 5-20x 压缩, 需额外依赖
- semantic: embedding 聚类合并 — 中等, 4-8x 压缩

当前实现: selective (主) + 摘要混合 (兜底).
后续可加 llmlingua 作为可选升级.

设计原则:
1. 压缩不丢失关键信息 (实体/数字/客户问题)
2. 压缩后 LLM 仍能正确答复 (质量分 >= min_quality_score)
3. 失败回退: 压缩后质量不达标, 返回原文
4. 指标可观测: compression_ratio / quality_score / latency
"""

from __future__ import annotations

import re
import time
from typing import Any

from lumio.shared.config import CompressionSettings, get_settings
from lumio.shared.logger import get_logger
from lumio.shared.metrics import (
    CONTEXT_COMPRESSION_LATENCY,
    CONTEXT_COMPRESSION_RATIO,
)

logger = get_logger(__name__)


# 中文停用词 (客服场景定制)
_STOP_WORDS_ZH = {
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没",
    "看", "好", "自己", "这", "那", "里", "为", "么", "什么", "怎么", "如何",
    "请问", "请", "您", "我们", "咱们", "吧", "呢", "啊", "嗯", "哦", "哈",
    "这个", "那个", "一下", "一些", "一直", "已经", "可以", "可能",
    "应该", "或者", "如果", "因为", "所以", "但是", "不过", "然后", "于是",
    "比较", "大概", "差不多", "基本", "主要", "一般", "另外", "其他", "之类",
}

# 英文停用词
_STOP_WORDS_EN = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "to", "of", "in", "on", "at", "by", "for", "with", "about", "as", "into",
    "and", "or", "but", "if", "then", "so", "than", "that", "this", "these",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us",
    "my", "your", "his", "its", "our", "their", "what", "which", "who", "whom",
    "do", "does", "did", "have", "has", "had", "will", "would", "should", "could",
    "can", "may", "might", "must", "shall", "am", "just", "also", "very", "really",
}

# 关键信息保护词性: 数字 / 金额 / 卡号 / 日期 / 专有名词
_PROTECTED_PATTERNS = [
    re.compile(r"\d{4,}"),  # 4 位以上数字 (卡号/金额/日期)
    re.compile(r"\d+\.\d+"),  # 小数
    re.compile(r"[¥$￥]\s*[\d,.]+"),  # 货币
    re.compile(r"\d{1,2}[-/年月]\d{1,2}[-/日]?"),  # 日期
    re.compile(r"[A-Z]{2,}\d+"),  # 产品代码 (VISA1234)
    re.compile(r"[\u4e00-\u9fff]{2,8}卡"),  # XX卡
    re.compile(r"[\u4e00-\u9fff]{2,6}分[期款]"),  # XX分期
    re.compile(r"密码|验证码|身份证|额度|账单|积分|利率|手续费|违约金"),  # 金融术语
]

# 句子边界 (中英文兼容)
_SENT_SPLIT = re.compile(r"(?<=[.!?。!?;；\n])\s*")


def _is_stop_word(token: str) -> bool:
    """判断是否为停用词 (中英双语)."""
    t = token.lower().strip()
    if not t:
        return True
    if t in _STOP_WORDS_ZH or t in _STOP_WORDS_EN:
        return True
    # 单字符非汉字非字母, 标点
    if len(t) == 1 and not t.isalnum() and not ("\u4e00" <= t <= "\u9fff"):
        return True
    return False


def _is_protected(token: str) -> bool:
    """判断是否需要保护 (关键信息, 不能压缩)."""
    for pat in _PROTECTED_PATTERNS:
        if pat.search(token):
            return True
    return False


def _tokenize_zh(text: str) -> list[str]:
    """轻量中文分词 (按字符 + 标点切分, 不用 jieba 避免重依赖).

    输出: 单词/汉字/标点的 token 列表
    """
    tokens: list[str] = []
    current: list[str] = []
    for ch in text:
        if ch.isspace():
            if current:
                tokens.append("".join(current))
                current = []
        elif "\u4e00" <= ch <= "\u9fff":
            # 汉字: 单字为 token
            if current:
                tokens.append("".join(current))
                current = []
            tokens.append(ch)
        elif ch.isalnum():
            current.append(ch)
        else:
            # 标点
            if current:
                tokens.append("".join(current))
                current = []
            tokens.append(ch)
    if current:
        tokens.append("".join(current))
    return tokens


def _token_importance(token: str, sent_idx: int, total_sents: int, freq: dict[str, int]) -> float:
    """计算 token 重要性分数 (0~1).

    规则:
    - 停用词: 0
    - 保护词 (数字/金额/卡号): 1
    - 罕见词 (freq=1): 0.9
    - 常见词 (freq>=3): 0.3
    - 句首/句末: 加权 0.1
    """
    if _is_stop_word(token):
        return 0.0
    if _is_protected(token):
        return 1.0
    f = freq.get(token.lower(), 0)
    base = 0.9 if f <= 1 else (0.6 if f == 2 else 0.3)
    # 句首/句末略加权
    if sent_idx == 0 or sent_idx == total_sents - 1:
        base = min(1.0, base + 0.1)
    return base


class SelectiveCompressor:
    """Selective Context 算法实现 (无需外部模型).

    步骤:
    1. 分句 (中英文标点)
    2. 分词 + 计算词频
    3. 计算每个 token 重要性
    4. 按重要性排序, 保留 top-K
    5. 按原文顺序重组

    压缩比: 默认 3-5x (即 1000 tokens → 200-300 tokens)
    """

    def __init__(self, settings: CompressionSettings | None = None) -> None:
        self._settings = settings or get_settings().compression

    def compress(self, text: str) -> tuple[str, dict[str, Any]]:
        """压缩文本.

        Returns:
            (compressed_text, stats)
            stats = {
                "original_tokens": int,
                "compressed_tokens": int,
                "ratio": float,
                "kept_tokens": int,
                "dropped_tokens": int,
            }
        """
        if not text or not self._settings.enabled:
            return text, {"original_tokens": _estimate(text), "compressed_tokens": _estimate(text), "ratio": 1.0}

        _start = time.monotonic()
        original_tokens = _estimate(text)

        # 1. 分句
        sents = [s for s in _SENT_SPLIT.split(text) if s.strip()]
        if len(sents) <= 2:
            # 太短, 不压缩
            return text, {
                "original_tokens": original_tokens,
                "compressed_tokens": original_tokens,
                "ratio": 1.0,
            }

        # 2. 分词 + 词频
        all_tokens: list[tuple[str, int]] = []  # (token, sent_idx)
        freq: dict[str, int] = {}
        for i, sent in enumerate(sents):
            tokens = _tokenize_zh(sent)
            for tok in tokens:
                all_tokens.append((tok, i))
                t = tok.lower()
                if not _is_stop_word(t) and not _is_protected(t):
                    freq[t] = freq.get(t, 0) + 1

        # 3. 计算重要性
        scored: list[tuple[str, float, int]] = []  # (token, score, sent_idx)
        for tok, sent_idx in all_tokens:
            score = _token_importance(tok, sent_idx, len(sents), freq)
            scored.append((tok, score, sent_idx))

        # 4. 保留 top-K (按 preserve_ratio)
        target_kept = max(1, int(len(scored) * self._settings.preserve_ratio))
        # 按重要性降序排序
        scored.sort(key=lambda x: x[1], reverse=True)
        kept_set = {id(s) for s in scored[:target_kept]}

        # 5. 按原文顺序重组 (保留句子的所有 token, 不打断句子)
        # 策略: 保留所有被标记为 keep 的 token, 移除相邻停用词
        # 为保持可读性, 整句保留得分 >= 阈值 (句子平均分)
        sent_scores: list[float] = []
        for i, sent in enumerate(sents):
            sent_tokens = [s for s in scored if s[2] == i]
            if not sent_tokens:
                sent_scores.append(0.0)
            else:
                sent_scores.append(sum(s[1] for s in sent_tokens) / len(sent_tokens))

        # 保留高得分句子
        threshold = 0.4
        kept_sents = [sents[i] for i, sc in enumerate(sent_scores) if sc >= threshold]

        if not kept_sents:
            kept_sents = sents[: max(1, len(sents) // 2)]

        compressed = " ".join(kept_sents)
        compressed_tokens = _estimate(compressed)
        ratio = original_tokens / max(compressed_tokens, 1)

        elapsed = time.monotonic() - _start
        CONTEXT_COMPRESSION_RATIO.labels(algorithm="selective").observe(ratio)
        CONTEXT_COMPRESSION_LATENCY.labels(algorithm="selective").observe(elapsed)

        logger.debug(
            "Selective 压缩: 原文 %d tokens, 压缩后 %d tokens, ratio=%.2f, latency=%.3fs",
            original_tokens,
            compressed_tokens,
            ratio,
            elapsed,
        )

        return compressed, {
            "original_tokens": original_tokens,
            "compressed_tokens": compressed_tokens,
            "ratio": ratio,
            "kept_sentences": len(kept_sents),
            "total_sentences": len(sents),
            "elapsed_seconds": elapsed,
        }


def _estimate(text: str) -> int:
    """快速 token 估算 — 委托给 lumio.shared.token_utils."""
    from lumio.shared.token_utils import estimate_tokens as _et

    return _et(text)


def compress_history(history: list[dict[str, str]], max_tokens: int = 1500) -> list[dict[str, str]]:
    """压缩历史消息 (供 bot_agent._load_history 调用).

    当历史总 token 超过 max_tokens, 对最早 50% 的轮次执行压缩,
    保留最近 50% 原样. 压缩结果以新 user/assistant 消息注入.
    """
    settings = get_settings().compression
    if not settings.enabled:
        return history

    # 计算总 token
    total = sum(_estimate(m.get("content", "")) for m in history)
    if total <= max_tokens:
        return history

    # 分割: 前半压缩, 后半保留
    split = len(history) // 2
    to_compress = history[:split]
    to_keep = history[split:]

    compressor = SelectiveCompressor(settings)
    compressed_msgs: list[dict[str, str]] = []
    for msg in to_compress:
        content = msg.get("content", "")
        compressed_content, stats = compressor.compress(content)
        # P1-1 上下文工程修复: min_quality_score 落地 — 旧配置从未被读取.
        # 质量代理 = 保留句子占比 (kept/total), 归一化到 1-5 与 min_quality_score 比较.
        # 注意: "太短不压缩"路径的 stats 无句子字段, 用 .get 兜底 (第五轮修复)
        kept_sentences = stats.get("kept_sentences", 0)
        total_sentences = stats.get("total_sentences", 0)
        kept_ratio = kept_sentences / total_sentences if total_sentences else 0.0
        quality = kept_ratio * 5.0
        if (
            stats["ratio"] >= settings.target_ratio
            and stats["compressed_tokens"] < _estimate(content) * 0.8
            and quality >= settings.min_quality_score
        ):
            compressed_msgs.append({**msg, "content": compressed_content, "_compressed": "selective"})
        else:
            # 压缩效果不达标 (压缩比不足 / 质量低于门槛), 保留原文
            compressed_msgs.append(msg)

    logger.info(
        "历史压缩: %d 轮压缩, %d 轮保留, 总 token %d → %d",
        len(to_compress),
        len(to_keep),
        total,
        sum(_estimate(m.get("content", "")) for m in compressed_msgs + to_keep),
    )
    return compressed_msgs + to_keep
