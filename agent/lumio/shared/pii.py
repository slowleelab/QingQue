"""PII 敏感信息脱敏

自动检测并脱敏文本中的敏感信息:
- 手机号: 138****1234
- 身份证号: 110***********1234
- 银行卡号: 6222****1234
- 邮箱: z***@example.com
- 密码字段: ******（字段名含 password/secret/token）

用于审计日志、结构化日志、API 响应过滤。
"""

from __future__ import annotations

import re

# ── 正则模式 ──

# 手机号: 1[3-9] 开头 + 9 位数字
_PHONE_PATTERN = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")

# 身份证号: 17 位数字 + 1 位校验位(数字或X)
_ID_CARD_PATTERN = re.compile(r"(?<!\d)(\d{6})\d{8}(\d{4})(?!\d)")

# 银行卡号: 16-19 位连续数字（前 4 后 4 保留）
_BANK_CARD_PATTERN = re.compile(r"(?<!\d)(\d{4})\d{8,11}(\d{4})(?!\d)")

# 邮箱
_EMAIL_PATTERN = re.compile(r"([a-zA-Z0-9._%+-])[a-zA-Z0-9._%+-]*@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})")

# 敏感字段名（JSON key 含这些词时，值替换为 ******）
_SENSITIVE_KEY_PATTERN = re.compile(
    r'("(?:password|passwd|secret|token|api_key|apikey|private_key|cvv|pin)\s*"\s*:\s*")[^"]*(")',
    re.IGNORECASE,
)


def mask_phone(text: str) -> str:
    """脱敏手机号: 13812345678 → 138****5678"""
    return _PHONE_PATTERN.sub(r"\1****\2", text)


def mask_id_card(text: str) -> str:
    """脱敏身份证号: 110101199001011234 → 110101********1234"""
    return _ID_CARD_PATTERN.sub(r"\1********\2", text)


def mask_bank_card(text: str) -> str:
    """脱敏银行卡号: 6222021234567890 → 6222****7890"""
    return _BANK_CARD_PATTERN.sub(r"\1****\2", text)


def mask_email(text: str) -> str:
    """脱敏邮箱: zhangsan@example.com → z***@example.com"""
    return _EMAIL_PATTERN.sub(r"\1***@\2", text)


def mask_sensitive_fields(text: str) -> str:
    """脱敏 JSON 中的敏感字段值"""
    return _SENSITIVE_KEY_PATTERN.sub(r"\1******\2", text)


def mask_pii(text: str) -> str:
    """一键脱敏所有已知 PII 类型

    按顺序: 敏感字段 → 邮箱 → 身份证 → 银行卡 → 手机号
    （身份证/银行卡优先于手机号，避免 11 位数字被误判为手机号）
    """
    if not text:
        return text
    result = mask_sensitive_fields(text)
    result = mask_email(result)
    result = mask_id_card(result)
    result = mask_bank_card(result)
    result = mask_phone(result)
    return result
