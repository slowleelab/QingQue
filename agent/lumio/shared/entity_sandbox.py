"""A6: 上下文隔离 / 沙箱 — 实体池 PII 防护

问题诊断: session.py:98 _INCREMENTAL_FIELDS = {"intent_stack", "entity_pool"}
跨会话 (相同 session_id 重连 / 多设备同步) 增量合并时, 若 entity_pool 含
卡号 / 身份证 / 密码等敏感字段, 会自动注入下一会话 system_prompt.

修复策略:
1. 白名单: 仅允许非 PII 的 entity_type 跨会话带入 (card_type, vip_level, risk_tolerance, city, occupation)
2. 黑名单: card_number / id_number / phone / cvv / password / address 强制单会话, 跨会话清空
3. 检测点:
   - 跨会话 merge: 持久化前过白名单
   - 跨会话 load: 读取后过白名单
   - system_prompt 注入: 输出前过白名单
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from lumio.shared.config import get_settings
from lumio.shared.logger import get_logger

logger = get_logger(__name__)


# 默认白名单 (非 PII 实体, 可跨会话带入)
DEFAULT_ENTITY_ALLOWLIST: frozenset[str] = frozenset(
    {
        "card_type",  # 卡种 (VISA/银联)
        "vip_level",  # VIP 等级
        "risk_tolerance",  # 风险偏好 R1-R4
        "city",  # 城市
        "occupation",  # 职业
        "age_range",  # 年龄段
        "product_interest",  # 产品兴趣
    }
)

# 默认黑名单 (PII 敏感, 跨会话清空)
DEFAULT_ENTITY_DENYLIST: frozenset[str] = frozenset(
    {
        "card_number",  # 卡号
        "id_number",  # 身份证号
        "phone",  # 手机号
        "cvv",  # 卡背 3 位
        "password",  # 密码
        "pin",  # PIN 码
        "address",  # 地址
        "email",  # 邮箱
        "name",  # 姓名
        "birthday",  # 生日
        "bank_account",  # 银行账号
        "verification_code",  # 验证码
    }
)

# PII 模式 (在 value 中检测, 防止 entity_type 误标)
_PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "card_number": re.compile(r"\b\d{16,19}\b"),
    "id_number": re.compile(r"\b\d{17}[\dXx]\b"),
    "phone": re.compile(r"\b1[3-9]\d{9}\b"),
    "cvv": re.compile(r"\b\d{3,4}\b"),  # 太宽泛, 仅在 entity_type 提示时检查
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "bank_account": re.compile(r"\b\d{16,21}\b"),
}


class EntitySandbox(BaseModel):
    """实体沙箱 - 跨会话过滤的实体."""

    entity_type: str
    value: str
    confidence: float = 1.0
    source_turn_id: str | None = None
    created_at: float = Field(default_factory=lambda: 0.0)
    is_pii: bool = False  # 标记是否 PII (调试用)

    model_config = {"frozen": False}


def is_pii_entity_type(entity_type: str) -> bool:
    """判断 entity_type 是否为 PII (黑名单)."""
    if not entity_type:
        return False
    return entity_type.lower() in {e.lower() for e in DEFAULT_ENTITY_DENYLIST}


def detect_pii_in_value(value: str) -> str | None:
    """在 value 字符串中检测 PII 模式, 返回匹配类型或 None."""
    if not value:
        return None
    for pii_type, pattern in _PII_PATTERNS.items():
        if pattern.search(value):
            return pii_type
    return None


def filter_for_cross_session(entities: list[Any]) -> list[Any]:
    """跨会话持久化前过滤: 移除 PII entity, 保留白名单 entity.

    Args:
        entities: list[Entity] (从 SessionState 来的)

    Returns:
        过滤后的 entity 列表 (PII 实体被移除, 留下非 PII 白名单实体)

    防御场景:
    - 客户上轮输入卡号 → entity_pool 增加 card_number
    - session 跨设备/刷新重连 → entity_pool 跨会话 merge
    - 修复前: 卡号被拼到下一轮 system_prompt
    - 修复后: card_number 被过滤, 不进入持久化层
    """
    if not entities:
        return []

    allowlist = set(get_settings().guard.entity_pool_allowlist)
    denylist = DEFAULT_ENTITY_DENYLIST

    safe: list[Any] = []
    removed_count = 0
    for e in entities:
        et = getattr(e, "entity_type", "") or ""
        ev = getattr(e, "value", "") or ""

        # 1. 黑名单直接拒绝
        if et.lower() in {d.lower() for d in denylist}:
            removed_count += 1
            logger.debug("实体黑名单过滤: type=%s, value=%s", et, ev[:20] + "..." if len(ev) > 20 else ev)
            continue

        # 2. 白名单允许
        if et.lower() in {a.lower() for a in allowlist}:
            # 二次校验: 即使 entity_type 在白名单, value 也可能含 PII
            detected_pii = detect_pii_in_value(ev)
            if detected_pii and detected_pii not in ("cvv",):  # cvv 太宽泛, 跳过
                removed_count += 1
                logger.warning(
                    "实体白名单但 value 含 PII 模式, 过滤: type=%s detected=%s",
                    et,
                    detected_pii,
                )
                continue
            safe.append(e)
            continue

        # 3. 未知类型: 保守拒绝 (防 entity_type 误用绕过白名单)
        removed_count += 1
        logger.debug("实体未知类型, 保守过滤: type=%s", et)

    if removed_count > 0:
        logger.info("跨会话 entity 过滤: 移除 %d 个 PII/未知实体, 保留 %d 个", removed_count, len(safe))

    return safe


def mask_pii_in_text(text: str) -> str:
    """在任意文本中遮蔽 PII 模式 (作为保险, 不替代 entity 层过滤).

    用途: 历史消息 / RAG 内容中可能含 PII, 写入 system_prompt 前过此函数.
    """
    if not text:
        return text
    masked = text
    masked = _PII_PATTERNS["card_number"].sub("****CARD****", masked)
    masked = _PII_PATTERNS["id_number"].sub("****ID****", masked)
    masked = _PII_PATTERNS["phone"].sub("****PHONE****", masked)
    masked = _PII_PATTERNS["email"].sub("****EMAIL****", masked)
    masked = _PII_PATTERNS["bank_account"].sub("****BANK****", masked)
    return masked
