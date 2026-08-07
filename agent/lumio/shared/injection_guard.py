"""A3-A5: 提示词注入防御 (3 层)

3 个注入向量:
- A3 User Input 层: 客户直接输入的恶意指令
- A4 RAG Content 层: 知识库/检索内容被污染
- A5 Tool Result 层: MCP tool 返回结果含恶意指令

防御策略 (每层独立):
- Layer 1: 正则模式匹配 (轻量, 快, 召回高)
- Layer 2: 结构化检测 (角色混淆, prompt 结构)
- Layer 3: Guard LLM 二次校验 (昂贵, 默认关, 灰度开)

动作分级:
- reject: 直接拒绝 (用户输入命中明确注入)
- sanitize: 净化 (RAG / tool 内容移除指令性语句, 保留信息)
- quarantine: 隔离 (可疑但不确定, 标记后人工审核)
- pass: 放行
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from lumio.shared.config import GuardrailSettings, get_settings
from lumio.shared.logger import get_logger
from lumio.shared.metrics import INJECTION_ATTEMPTS, INJECTION_BLOCKED
from lumio.shared.pii import mask_pii

logger = get_logger(__name__)


class InjectionAction(str, Enum):
    """防御动作."""

    PASS = "pass"
    REJECT = "rejected"  # 仅 user input 层
    SANITIZE = "sanitized"  # RAG / tool 层
    QUARANTINE = "quarantined"  # 可疑, 隔离待审


class InjectionLayer(str, Enum):
    USER_INPUT = "user_input"
    RAG_CONTENT = "rag_content"
    TOOL_RESULT = "tool_result"


@dataclass
class InjectionVerdict:
    """注入检测结果."""

    action: InjectionAction
    layer: InjectionLayer
    pattern: str
    confidence: float
    sanitized_content: str | None = None
    reason: str = ""

    @property
    def is_blocked(self) -> bool:
        return self.action != InjectionAction.PASS


# ── Layer 1: 正则模式库 (20+ 已知 injection 关键词) ──

_USER_INPUT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 经典 prompt injection
    (
        re.compile(r"忽略\s*(以上|之前|上面|前述)\s*(所有|全部)?\s*(指令|提示|规则|约束|说明)", re.I),
        "ignore_previous_cn",
    ),
    (
        re.compile(r"ignore\s+(all\s+)?(previous|above|prior|system)\s+(instructions?|prompts?|rules?)", re.I),
        "ignore_previous_en",
    ),
    (re.compile(r"disregard\s+(all\s+)?(previous|above)\s+", re.I), "disregard_en"),
    (re.compile(r"forget\s+(everything|all)\s+(you|about)", re.I), "forget_en"),
    # 角色劫持
    (re.compile(r"你\s*(现在|从现在起|从此刻)\s*(是|扮演|变成|成为)", re.I), "role_hijack_cn"),
    (re.compile(r"you\s+are\s+(now|going\s+to\s+be|from\s+now)", re.I), "role_hijack_en"),
    (re.compile(r"扮演\s*[一乍]?\s*(个|位)?\s*(黑客|管理员|系统|root|admin|dan)", re.I), "role_play_jailbreak_cn"),
    (re.compile(r"act\s+as\s+(a\s+)?(hacker|admin|root|system|dan|jailbreak)", re.I), "role_play_jailbreak_en"),
    # 越权请求
    (
        re.compile(r"给我\s*(一|1|十|100|1000|一万|10000|百万|1000000|亿)\s*[万千百]?\s*(额度|钱|金额|分期)", re.I),
        "amount_injection_cn",
    ),
    (re.compile(r"transfer\s+\$?\d+[kmb]?\s+to", re.I), "amount_injection_en"),
    # 系统指令泄露
    (
        re.compile(r"(print|show|reveal|output|重复)\s+(your\s+)?(system\s+)?(prompt|instructions?|initial)", re.I),
        "prompt_leak_en",
    ),
    (re.compile(r"输出\s*(你|您的)?\s*(系统|原始|完整)\s*(提示|指令|规则|prompt)", re.I), "prompt_leak_cn"),
    # Jailbreak 模板
    (re.compile(r"DAN|do\s+anything\s+now", re.I), "dan_jailbreak"),
    (re.compile(r"jailbreak|bypass\s+safety", re.I), "jailbreak_keyword"),
    (re.compile(r"developer\s+mode|god\s+mode", re.I), "developer_mode"),
    # 银行场景特定
    (
        re.compile(r"(请|帮我|求你|务必)\s*(把|把卡|帮我把)\s*(卡|钱|额度|密码)\s*(给我|转|改成|变为)", re.I),
        "bank_specific_cn",
    ),
    (re.compile(r"(set|change|reset)\s+(my\s+)?(password|pin|credit\s+limit)\s+to", re.I), "bank_specific_en"),
]

_RAG_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 检索内容中不应含指令
    (re.compile(r"^\s*(请|必须|务必|一定)\s*(回答|告诉|告知|输出|返回|执行)", re.I), "rag_directive_cn"),
    (
        re.compile(r"^\s*(you\s+must|please\s+answer|tell\s+the\s+user|return\s+the\s+following)", re.I),
        "rag_directive_en",
    ),
    (re.compile(r"[\u4e00-\u9fff]*?(忽略|忘记)\s*(之前|以上)", re.I), "rag_injection_ignore"),
    (re.compile(r"<\|im_start\|>|<\|im_end\|>|<<SYS>>|<</SYS>>", re.I), "rag_special_tokens"),
    (re.compile(r"\[\s*系统\s*\]|\[\s*system\s*\]|\[\s*assistant\s*\]", re.I), "rag_role_tag"),
]

_TOOL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # tool 返回中不应含新指令
    (re.compile(r"(请|务必|一定|现在|立刻)\s*(告诉|通知|回复|告诉客户|答复客户)", re.I), "tool_directive_cn"),
    (
        re.compile(
            r"(please\s+)?(now|immediately|urgent)\s+(tell|reply|inform|notify)\s+(the\s+)?(user|customer)", re.I
        ),
        "tool_directive_en",
    ),
    # 编码注入 (base64/hex 指令)
    (re.compile(r"data:text/plain;base64,[A-Za-z0-9+/=]{50,}", re.I), "tool_base64_injection"),
]


# ── Layer 2: 角色混淆 / Prompt 结构检测 ──

_ROLE_CONFUSION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"<system\s*>|</system\s*>", re.I), "system_tag"),
    (re.compile(r"###\s*(instruction|system|prompt|input|response):?", re.I), "markdown_prompt"),
    (re.compile(r"<\s*/?functioncall\s*>", re.I), "function_call_tag"),
    (re.compile(r"```\s*(system|prompt|instruction)", re.I), "code_block_prompt"),
    (re.compile(r"Human:|Assistant:|System:\s*$", re.I | re.M), "anthropic_role"),
    (re.compile(r"<\|.*?\|>", re.I), "special_token_format"),
]


# ── 3 层独立 pattern (per-layer 命名空间, 避免 layer 间误判) ──
# 同样的 <system> tag 在 user input / RAG / tool_result 语义不同:
# - user: 攻击, REJECT
# - RAG: 内容污染, SANITIZE
# - tool: 数据格式问题, 记录但不阻断

_ROLE_CONFUSION_USER = _ROLE_CONFUSION_PATTERNS  # 用户输入: 全部用同一组
_ROLE_CONFUSION_RAG = _ROLE_CONFUSION_PATTERNS  # RAG 净化: 同组, 但 verdict 会带 layer="rag" 前缀
_ROLE_CONFUSION_TOOL = _ROLE_CONFUSION_PATTERNS  # Tool 净化: 同组, 但 verdict 会带 layer="tool" 前缀


@dataclass
class InjectionGuard:
    """注入检测守卫 (单例)."""

    user_input_enabled: bool = True
    rag_sanitizer_enabled: bool = True
    tool_sanitizer_enabled: bool = True
    layer1_regex: bool = True
    layer2_role_confusion: bool = True
    layer3_guard_llm: bool = False
    user_patterns: list[tuple[re.Pattern[str], str]] = field(default_factory=lambda: _USER_INPUT_PATTERNS)
    rag_patterns: list[tuple[re.Pattern[str], str]] = field(default_factory=lambda: _RAG_PATTERNS)
    tool_patterns: list[tuple[re.Pattern[str], str]] = field(default_factory=lambda: _TOOL_PATTERNS)
    role_confusion_patterns: list[tuple[re.Pattern[str], str]] = field(default_factory=lambda: _ROLE_CONFUSION_PATTERNS)

    @classmethod
    def from_settings(cls, settings: GuardrailSettings | None = None) -> InjectionGuard:
        s = settings or get_settings().guard
        return cls(
            user_input_enabled=s.user_input_enabled,
            rag_sanitizer_enabled=s.rag_content_sanitizer_enabled,
            tool_sanitizer_enabled=s.tool_result_sanitizer_enabled,
            layer1_regex=s.user_input_layer1_regex,
            layer2_role_confusion=s.user_input_layer2_role_confusion,
            layer3_guard_llm=s.user_input_layer3_guard_llm,
        )

    def check_user_input(self, text: str) -> InjectionVerdict:
        """A3: 检查用户输入.

        - Layer 1 正则命中 → reject
        - Layer 2 角色混淆 → reject
        - Layer 3 Guard LLM (未启用) → 跳过
        """
        if not self.user_input_enabled or not text:
            return InjectionVerdict(
                action=InjectionAction.PASS,
                layer=InjectionLayer.USER_INPUT,
                pattern="disabled",
                confidence=0.0,
            )

        # Layer 1: 正则
        if self.layer1_regex:
            for pattern, name in self.user_patterns:
                if pattern.search(text):
                    INJECTION_ATTEMPTS.labels(layer="user_input", pattern=name).inc()
                    INJECTION_BLOCKED.labels(layer="user_input", action="rejected").inc()
                    logger.warning(
                        "User input 注入拦截: pattern=%s text=%s",
                        name,
                        mask_pii(text[:100]),  # PII 脱敏后再记日志, 防卡号/身份证泄露
                    )
                    return InjectionVerdict(
                        action=InjectionAction.REJECT,
                        layer=InjectionLayer.USER_INPUT,
                        pattern=name,
                        confidence=0.95,
                        reason=f"命中已知注入模式: {name}",
                    )

        # Layer 2: 角色混淆
        if self.layer2_role_confusion:
            for pattern, name in self.role_confusion_patterns:
                if pattern.search(text):
                    INJECTION_ATTEMPTS.labels(layer="user_input", pattern=name).inc()
                    INJECTION_BLOCKED.labels(layer="user_input", action="rejected").inc()
                    logger.warning("User input 角色混淆拦截: pattern=%s", name)
                    return InjectionVerdict(
                        action=InjectionAction.REJECT,
                        layer=InjectionLayer.USER_INPUT,
                        pattern=name,
                        confidence=0.9,
                        reason=f"角色混淆: {name}",
                    )

        # Layer 3: Guard LLM (灰度期)
        if self.layer3_guard_llm:
            verdict = self._check_with_guard_llm(text, InjectionLayer.USER_INPUT)
            if verdict.is_blocked:
                return verdict

        return InjectionVerdict(
            action=InjectionAction.PASS,
            layer=InjectionLayer.USER_INPUT,
            pattern="none",
            confidence=0.0,
        )

    def sanitize_rag_content(self, text: str) -> tuple[str, InjectionVerdict]:
        """A4: 净化 RAG 检索内容.

        策略: 检测指令性语句, 替换为占位符, 保留信息.
        返回: (sanitized_text, verdict)
        """
        if not self.rag_sanitizer_enabled or not text:
            return text, InjectionVerdict(
                action=InjectionAction.PASS,
                layer=InjectionLayer.RAG_CONTENT,
                pattern="disabled",
                confidence=0.0,
            )

        sanitized = text
        matched = False
        first_pattern = ""
        first_confidence = 0.0

        for pattern, name in self.rag_patterns:
            if pattern.search(sanitized):
                # 替换为脱敏标记 (不暴露 pattern 名, 防信息泄露)
                sanitized = pattern.sub("[已净化]", sanitized)
                if not matched:
                    matched = True
                    first_pattern = name
                    first_confidence = 0.85
                    INJECTION_ATTEMPTS.labels(layer="rag_content", pattern=name).inc()

        # 角色混淆检测 (RAG 同样适用)
        for pattern, name in self.role_confusion_patterns:
            if pattern.search(sanitized):
                sanitized = pattern.sub("[已净化]", sanitized)
                if not matched:
                    matched = True
                    first_pattern = name
                    first_confidence = 0.85
                    INJECTION_ATTEMPTS.labels(layer="rag_content", pattern=name).inc()

        if matched:
            INJECTION_BLOCKED.labels(layer="rag_content", action="sanitized").inc()
            logger.info("RAG 内容净化: pattern=%s", first_pattern)
            return sanitized, InjectionVerdict(
                action=InjectionAction.SANITIZE,
                layer=InjectionLayer.RAG_CONTENT,
                pattern=first_pattern,
                confidence=first_confidence,
                sanitized_content=sanitized,
                reason=f"RAG 内容含指令性语句, 已净化: {first_pattern}",
            )

        return text, InjectionVerdict(
            action=InjectionAction.PASS,
            layer=InjectionLayer.RAG_CONTENT,
            pattern="none",
            confidence=0.0,
        )

    def sanitize_tool_result(self, tool_name: str, result: Any) -> tuple[Any, InjectionVerdict]:
        """A5: 净化 Tool 返回结果.

        策略:
        - 字符串字段过指令过滤
        - 限制返回大小 (防 prompt flooding)
        - 数字字段类型校验
        - user_facing 字段单独标记
        """
        if not self.tool_sanitizer_enabled:
            return result, InjectionVerdict(
                action=InjectionAction.PASS,
                layer=InjectionLayer.TOOL_RESULT,
                pattern="disabled",
                confidence=0.0,
            )

        settings = get_settings().guard
        max_size = settings.tool_result_max_size_bytes

        cleaned, matched_pattern = self._sanitize_recursive(result, max_size, depth=0)
        if matched_pattern:
            INJECTION_BLOCKED.labels(layer="tool_result", action="sanitized").inc()
            INJECTION_ATTEMPTS.labels(layer="tool_result", pattern=matched_pattern).inc()
            logger.warning("Tool 结果净化: tool=%s pattern=%s", tool_name, matched_pattern)
            return cleaned, InjectionVerdict(
                action=InjectionAction.SANITIZE,
                layer=InjectionLayer.TOOL_RESULT,
                pattern=matched_pattern,
                confidence=0.85,
                sanitized_content="<sanitized>",
                reason=f"Tool 返回含指令, 已净化: {matched_pattern}",
            )

        return cleaned, InjectionVerdict(
            action=InjectionAction.PASS,
            layer=InjectionLayer.TOOL_RESULT,
            pattern="none",
            confidence=0.0,
        )

    def _sanitize_recursive(self, obj: Any, max_size: int, depth: int) -> tuple[Any, str | None]:
        """递归净化 dict/list/str, 检测注入并替换."""
        if depth > 8:
            return obj, None
        if isinstance(obj, str):
            if len(obj.encode("utf-8")) > max_size:
                obj = obj[:max_size] + "...[已截断]"
            for pattern, name in self.tool_patterns:
                if pattern.search(obj):
                    return "[已净化]", name  # 不暴露 pattern 名
            return obj, None
        if isinstance(obj, dict):
            out: dict[str, Any] = {}
            matched: str | None = None
            for k, v in obj.items():
                clean_v, m = self._sanitize_recursive(v, max_size, depth + 1)
                out[k] = clean_v
                if m and not matched:
                    matched = m
            return out, matched
        if isinstance(obj, list):
            out_list: list[Any] = []
            matched = None
            for item in obj:
                clean_item, m = self._sanitize_recursive(item, max_size, depth + 1)
                out_list.append(clean_item)
                if m and not matched:
                    matched = m
            return out_list, matched
        return obj, None

    def _check_with_guard_llm(self, text: str, layer: InjectionLayer) -> InjectionVerdict:
        """Layer 3: Guard LLM 二次校验 (默认关闭, 灰度期启用).

        使用 Qwen2.5-0.5B 等轻量模型分类, confidence < 0.7 放行.
        """
        # 实际实现需要注入 LLMClient, 此处占位
        # 返回 PASS 让调用方继续
        return InjectionVerdict(
            action=InjectionAction.PASS,
            layer=layer,
            pattern="guard_llm_disabled",
            confidence=0.0,
        )


# 全局单例
_guard: InjectionGuard | None = None


def get_guard() -> InjectionGuard:
    """获取全局 InjectionGuard 单例."""
    global _guard
    if _guard is None:
        _guard = InjectionGuard.from_settings()
    return _guard
