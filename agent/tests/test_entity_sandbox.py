"""实体沙箱 PII 防护单元测试 (entity_sandbox.py)"""

from __future__ import annotations

from dataclasses import dataclass

from lumio.shared.entity_sandbox import (
    EntitySandbox,
    detect_pii_in_value,
    filter_for_cross_session,
    is_pii_entity_type,
    mask_pii_in_text,
)

# ── EntitySandbox 模型 ──


def test_entity_sandbox_defaults():
    """缺省字段: confidence=1.0, is_pii=False"""
    e = EntitySandbox(entity_type="card_type", value="白金卡")
    assert e.confidence == 1.0
    assert e.source_turn_id is None
    assert not e.is_pii


def test_entity_sandbox_full():
    """完整字段构造"""
    e = EntitySandbox(
        entity_type="phone",
        value="13800138000",
        confidence=0.9,
        source_turn_id="t1",
        is_pii=True,
    )
    assert e.confidence == 0.9
    assert e.source_turn_id == "t1"
    assert e.is_pii


# ── PII 类型判断 ──


def test_is_pii_entity_type_hit():
    """黑名单类型判定为 PII"""
    assert is_pii_entity_type("card_number")
    assert is_pii_entity_type("PHONE")  # 大小写不敏感


def test_is_pii_entity_type_miss():
    """白名单/未知类型不是 PII"""
    assert not is_pii_entity_type("card_type")
    assert not is_pii_entity_type("unknown_type")
    assert not is_pii_entity_type("")


# ── value 内 PII 检测 ──


def test_detect_pii_in_value_card():
    """16-19 位数字 → 卡号"""
    assert detect_pii_in_value("卡号 6222021234567890123") == "card_number"


def test_detect_pii_in_value_phone():
    """11 位手机号 → phone"""
    assert detect_pii_in_value("手机 13800138000") == "phone"


def test_detect_pii_in_value_id():
    """18 位身份证 → id_number"""
    assert detect_pii_in_value("11010119900101123X") == "id_number"


def test_detect_pii_in_value_email():
    """邮箱 → email"""
    assert detect_pii_in_value("联系 a@b.com") == "email"


def test_detect_pii_in_value_none():
    """无 PII / 空值返回 None"""
    assert detect_pii_in_value("正常文本") is None
    assert detect_pii_in_value("") is None
    assert detect_pii_in_value(None) is None


# ── 跨会话过滤 ──


@dataclass
class _FakeEntity:
    entity_type: str
    value: str


def test_filter_keeps_allowlist():
    """白名单实体保留"""
    entities = [_FakeEntity("card_type", "白金卡"), _FakeEntity("city", "北京")]
    result = filter_for_cross_session(entities)
    assert result == entities


def test_filter_removes_denylist():
    """黑名单实体移除"""
    entities = [_FakeEntity("card_number", "6222021234567890")]
    result = filter_for_cross_session(entities)
    assert result == []


def test_filter_removes_unknown():
    """未知类型保守拒绝"""
    entities = [_FakeEntity("mystery_field", "x")]
    result = filter_for_cross_session(entities)
    assert result == []


def test_filter_allowlist_value_with_pii():
    """白名单类型但 value 含 PII 模式 → 过滤"""
    entities = [_FakeEntity("card_type", "13800138000")]  # 手机号被误标 card_type
    result = filter_for_cross_session(entities)
    assert result == []


def test_filter_empty():
    """空列表直接返回空"""
    assert filter_for_cross_session([]) == []
    assert filter_for_cross_session(None) == []


# ── 文本遮蔽 ──


def test_mask_pii_in_text():
    """文本中所有 PII 模式被遮蔽"""
    text = "卡号 6222021234567890123, 手机 13800138000, 邮箱 a@b.com"
    masked = mask_pii_in_text(text)
    assert "****CARD****" in masked
    assert "****PHONE****" in masked
    assert "****EMAIL****" in masked
    assert "6222021234567890123" not in masked


def test_mask_pii_in_text_plain():
    """无 PII 文本原样返回"""
    assert mask_pii_in_text("普通文本") == "普通文本"
    assert mask_pii_in_text("") == ""
    assert mask_pii_in_text(None) is None
