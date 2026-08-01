"""I3-C1 PII 检测与脱敏 (Luhn + GB11643)

架构评审 P2-4: PII 识别脱敏
架构评审 P2-5: 缓存 PII 隔离

支持:
- 信用卡号 (Luhn 校验, 13-19 位)
- 中国身份证号 (GB 11643-1999, 18 位 + 校验码)
- 中国手机号 (11 位, 1[3-9]xxxxxxxxx)
- 银行卡号 / 身份证号 / 手机号 / 邮箱 / IP

脱敏策略:
- 信用卡: 保留前 6 + 后 4 (BIN + 末四位), 中间 * 遮蔽
- 身份证: 保留前 6 + 后 4 (地区 + 末四位), 中间 * 遮蔽
- 手机号: 保留前 3 + 后 4 (号段 + 末四位)
- 邮箱: 用户名保留前 1 + *** + @域名
- IP: 末段脱敏 (192.168.x.x → 192.168.*.*)

设计:
- 不依赖具体业务, 提供 detect() / redact() / scan_and_redact() 三层 API
- 命中后输出 PIISpan, 记录位置/类型/原始值 (用于审计, 不入日志)
- 命中率高 + 误报低 (Luhn 校验是金标准)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class PIIType(str, Enum):
    """PII 类型枚举"""

    CREDIT_CARD = "credit_card"  # 信用卡 (Luhn 校验)
    ID_CARD_CN = "id_card_cn"  # 中国身份证 (GB 11643)
    MOBILE_CN = "mobile_cn"  # 中国手机号
    EMAIL = "email"
    IP_ADDR = "ip_addr"  # IPv4


@dataclass
class PIISpan:
    """PII 命中区间"""

    pii_type: PIIType
    start: int  # 在原文中的起始位置
    end: int  # 在原文中的结束位置 (exclusive)
    raw_value: str  # 原始值 (不写入日志, 仅内存使用)


# ── Luhn 算法 ──


def luhn_check(card_number: str) -> bool:
    """Luhn 校验 — 信用卡号金标准

    算法: 从右往左, 偶数位 ×2, 若 ≥10 则 -9, 全部求和, 能被 10 整除则合法
    """
    digits = [int(c) for c in card_number if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


# ── GB 11643 身份证校验 ──


_GB11643_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_GB11643_CHECK_CODES = ["1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2"]


def gb11643_check(id_number: str) -> bool:
    """GB 11643-1999 身份证号校验

    格式: 6 位地区码 + 8 位生日 (YYYYMMDD) + 3 位顺序码 + 1 位校验码
    """
    id_number = id_number.upper().strip()
    if len(id_number) != 18:
        return False
    if not re.match(r"^\d{17}[0-9X]$", id_number):
        return False

    # 校验码
    try:
        nums = [int(c) for c in id_number[:17]]
    except ValueError:
        return False
    total = sum(n * w for n, w in zip(nums, _GB11643_WEIGHTS))
    expected = _GB11643_CHECK_CODES[total % 11]
    return id_number[17] == expected


# ── 各种 PII 模式 ──


# 信用卡: 13-19 位数字, 可能有空格/连字符分隔
_CC_PATTERN = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
# 身份证: 18 位 (15 位旧版不处理)
_ID_PATTERN = re.compile(r"\b\d{17}[0-9Xx]\b")
# 中国手机号
_MOBILE_PATTERN = re.compile(r"\b1[3-9]\d{9}\b")
# 邮箱
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# IPv4
_IPV4_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _validate_credit_card(s: str) -> bool:
    """校验匹配到的串是不是真信用卡 (Luhn)"""
    digits_only = re.sub(r"[ -]", "", s)
    return luhn_check(digits_only)


def _validate_id_card(s: str) -> bool:
    return gb11643_check(s)


def _validate_ipv4(s: str) -> bool:
    parts = s.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def detect(text: str) -> list[PIISpan]:
    """检测文本中的 PII 区间

    返回所有命中, 按 start 位置排序.
    同位置重叠时, 优先保留更严格校验的类型 (credit_card > id_card > mobile > email > ip)
    """
    spans: list[PIISpan] = []

    # 1. 信用卡 (Luhn)
    for m in _CC_PATTERN.finditer(text):
        s = m.group()
        if _validate_credit_card(s):
            spans.append(PIISpan(PIIType.CREDIT_CARD, m.start(), m.end(), s))

    # 2. 身份证 (GB11643)
    for m in _ID_PATTERN.finditer(text):
        s = m.group()
        if _validate_id_card(s):
            spans.append(PIISpan(PIIType.ID_CARD_CN, m.start(), m.end(), s))

    # 3. 手机号
    for m in _MOBILE_PATTERN.finditer(text):
        spans.append(PIISpan(PIIType.MOBILE_CN, m.start(), m.end(), m.group()))

    # 4. 邮箱
    for m in _EMAIL_PATTERN.finditer(text):
        spans.append(PIISpan(PIIType.EMAIL, m.start(), m.end(), m.group()))

    # 5. IPv4
    for m in _IPV4_PATTERN.finditer(text):
        if _validate_ipv4(m.group()):
            spans.append(PIISpan(PIIType.IP_ADDR, m.start(), m.end(), m.group()))

    # 去重: 同位置只保留优先级最高的
    spans.sort(key=lambda x: (x.start, -_pii_priority(x.pii_type)))
    deduped: list[PIISpan] = []
    last_end = -1
    for span in spans:
        if span.start >= last_end:
            deduped.append(span)
            last_end = span.end
    return deduped


def _pii_priority(t: PIIType) -> int:
    return {
        PIIType.CREDIT_CARD: 5,
        PIIType.ID_CARD_CN: 4,
        PIIType.MOBILE_CN: 3,
        PIIType.EMAIL: 2,
        PIIType.IP_ADDR: 1,
    }.get(t, 0)


# ── 脱敏函数 ──


def redact_credit_card(s: str) -> str:
    """信用卡: 前 6 + * + 后 4

    例: 6222021234567890 → 622202******7890
    """
    digits = re.sub(r"[ -]", "", s)
    if len(digits) < 10:
        return "*" * len(s)
    return digits[:6] + "*" * (len(digits) - 10) + digits[-4:]


def redact_id_card(s: str) -> str:
    """身份证: 前 6 + ****** + 后 4

    例: 110101199003078888 → 110101********8888
    """
    if len(s) != 18:
        return "*" * len(s)
    return s[:6] + "*" * 8 + s[-4:]


def redact_mobile(s: str) -> str:
    """手机号: 前 3 + **** + 后 4

    例: 13800138000 → 138****8000
    """
    if len(s) != 11:
        return "*" * len(s)
    return s[:3] + "****" + s[-4:]


def redact_email(s: str) -> str:
    """邮箱: 保留前 1 + *** + @域名

    例: alice@example.com → a***@example.com
    """
    if "@" not in s:
        return "*" * len(s)
    user, domain = s.split("@", 1)
    if not user:
        return "*" * len(s)
    return user[0] + "***@" + domain


def redact_ip(s: str) -> str:
    """IP: 末两段脱敏

    例: 192.168.1.100 → 192.168.*.*
    """
    parts = s.split(".")
    if len(parts) != 4:
        return "*" * len(s)
    return ".".join(parts[:2] + ["*", "*"])


def redact(span: PIISpan) -> str:
    """对单个 PII span 脱敏"""
    fn = {
        PIIType.CREDIT_CARD: redact_credit_card,
        PIIType.ID_CARD_CN: redact_id_card,
        PIIType.MOBILE_CN: redact_mobile,
        PIIType.EMAIL: redact_email,
        PIIType.IP_ADDR: redact_ip,
    }.get(span.pii_type)
    if fn is None:
        return "*" * len(span.raw_value)
    return fn(span.raw_value)


def scan_and_redact(text: str) -> tuple[str, list[PIISpan]]:
    """扫描并脱敏 — 一站式 API

    返回: (脱敏后文本, 命中列表)
    """
    spans = detect(text)
    if not spans:
        return text, []

    # 从后往前替换, 避免位置偏移
    out = text
    for span in reversed(spans):
        out = out[: span.start] + redact(span) + out[span.end :]
    return out, list(reversed(spans))
