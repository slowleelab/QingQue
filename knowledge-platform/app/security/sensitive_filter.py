"""敏感词过滤 — AC 自动机 (pyahocorasick)

用户宗旨明确要求：敏感词匹配必须用 AC 自动机，不能用简单正则。
用于文档入库前扫描，命中敏感词则拒绝入库或标记告警。

AC 自动机优势：
- O(n) 时间复杂度扫描全文，与词典大小无关
- 一次扫描匹配所有敏感词，不随词典增长而变慢
- 支持重叠匹配（"信用卡盗刷" 和 "盗刷" 同时命中）
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import ahocorasick

from app.logging import get_logger

logger = get_logger(__name__)

# 默认敏感词（银行场景）
_DEFAULT_SENSITIVE_WORDS: list[str] = [
    # 敏感操作
    "盗刷", "诈骗", "洗钱", "套现", "伪冒",
    # 违规营销
    "包过", "内部渠道", "走后门",
    # 政治敏感
    "法轮功", "六四", "台独",
    # 个人隐私（入库时不应包含完整隐私数据）
    "身份证号", "银行卡号",
]


class SensitiveWordFilter:
    """AC 自动机敏感词过滤器

    使用 pyahocorasick 构建 AC 自动机，O(n) 扫描全文。
    支持从文件加载敏感词词典，运行时热替换。
    """

    def __init__(self, words: list[str] | None = None) -> None:
        self._words: list[str] = words or list(_DEFAULT_SENSITIVE_WORDS)
        self._automaton: ahocorasick.Automaton | None = None
        self._build()

    def _build(self) -> None:
        """构建 AC 自动机"""
        self._automaton = ahocorasick.Automaton()
        for idx, word in enumerate(self._words):
            if word and word.strip():
                self._automaton.add_word(word, (idx, word))
        self._automaton.make_automaton()
        logger.info("AC 自动机构建完成", word_count=len(self._words))

    def scan(self, text: str) -> list[dict[str, Any]]:
        """扫描文本，返回命中的敏感词列表

        Args:
            text: 待扫描文本

        Returns:
            命中列表，每项 {"word": "敏感词", "start": 起始位置, "end": 结束位置}
        """
        if not self._automaton or not text:
            return []

        hits: list[dict[str, Any]] = []
        seen: set[str] = set()

        for end_pos, (idx, word) in self._automaton.iter(text):
            start_pos = end_pos - len(word) + 1
            if word not in seen:
                hits.append({"word": word, "start": start_pos, "end": end_pos + 1})
                seen.add(word)

        return hits

    def contains_sensitive(self, text: str) -> bool:
        """快速判断是否包含敏感词"""
        if not self._automaton or not text:
            return False
        for _, (_, _) in self._automaton.iter(text):
            return True
        return False

    def mask(self, text: str, mask_char: str = "*") -> str:
        """脱敏：将敏感词替换为掩码字符"""
        if not self._automaton or not text:
            return text

        result = list(text)
        for end_pos, (_, word) in self._automaton.iter(text):
            start_pos = end_pos - len(word) + 1
            for i in range(start_pos, end_pos + 1):
                result[i] = mask_char
        return "".join(result)

    def reload(self, words: list[str]) -> None:
        """热替换敏感词词典"""
        self._words = words
        self._build()

    def load_from_file(self, file_path: str | Path) -> None:
        """从文件加载敏感词词典（每行一个词）"""
        path = Path(file_path)
        if not path.exists():
            logger.warning("敏感词文件不存在", path=str(path))
            return

        words: list[str] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                word = line.strip()
                if word and not word.startswith("#"):
                    words.append(word)

        if words:
            self.reload(words)
            logger.info("敏感词词典已加载", path=str(path), count=len(words))

    @property
    def word_count(self) -> int:
        return len(self._words)


# 全局单例
_filter: SensitiveWordFilter | None = None


def get_sensitive_filter() -> SensitiveWordFilter:
    """获取全局敏感词过滤器单例"""
    global _filter
    if _filter is None:
        from app.config import get_settings

        settings = get_settings()
        _filter = SensitiveWordFilter()
        if settings.security.sensitive_words_path:
            _filter.load_from_file(settings.security.sensitive_words_path)
    return _filter
