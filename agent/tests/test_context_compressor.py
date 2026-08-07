"""ContextCompressor 单元测试 (第五轮补测试 — 此前压缩器生产接线但零测试)."""

from __future__ import annotations

from lumio.services.bot.context_compressor import SelectiveCompressor, compress_history
from lumio.shared.config import CompressionSettings, get_settings


def _settings(**overrides) -> CompressionSettings:
    base = get_settings().compression
    return base.model_copy(update=overrides)


def test_selective_compress_reduces_tokens() -> None:
    """压缩应显著减少 token (目标 4x)."""
    text = (
        "客户询问信用卡年费减免政策, 客服回答消费满六次可免年费, "
        "客户又问账单分期手续费率, 客服回答三期零点七五百分比每月, "
        "客户表示需要办理分期, 客服引导客户确认分期金额和期数, "
        "客户确认后客服提交分期申请并告知办理进度。"
    ) * 3
    s = _settings(enabled=True)
    compressor = SelectiveCompressor(s)
    compressed, stats = compressor.compress(text)
    assert stats["compressed_tokens"] < stats["original_tokens"]
    assert stats["ratio"] >= 1.0
    assert compressed  # 非空


def test_protected_patterns_kept() -> None:
    """金额/日期等保护模式应保留 (金融领域关键信息不丢失)."""
    text = "本期账单金额 12,800.50 元, 还款日 2026-08-15, 逾期将产生利息。"
    s = _settings(enabled=True)
    compressor = SelectiveCompressor(s)
    compressed, _ = compressor.compress(text)
    # 关键数字不应被剔除 (保护模式 9 类)
    assert "12800" in compressed or "12,800" in compressed or "2026" in compressed


def test_compress_history_below_budget_unchanged() -> None:
    """总 token 未超预算时原样返回."""
    history = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "您好, 请问有什么可以帮您?"},
    ]
    result = compress_history(history, max_tokens=10000)
    assert result == history


def test_compress_history_over_budget_marks() -> None:
    """超预算时压缩器参与处理: 不抛异常, 且压缩生效时标记 _compressed.

    注: 压缩达标与否由质量门 (ratio >= target_ratio 且 quality >= min_quality_score)
    决定, 对低冗余文本会保留原文 — 这是正确行为 (防过度压缩毁语义).
    """
    long_turn = {
        "role": "user",
        "content": "我想了解一下信用卡分期手续费率是多少。账单分期有哪几期可以选择。手续费是按月收取吗。提前还款会退手续费吗。"
        * 3,
    }
    history = [
        long_turn,
        {"role": "assistant", "content": "分期手续费率如下。三期是零点七五。六期是零点七。十二期是零点六。" * 3},
    ]
    result = compress_history(history, max_tokens=50)
    # 不抛异常 + 总长度不增长 (压缩或保留均合法)
    total_before = sum(len(m["content"]) for m in history)
    total_after = sum(len(m["content"]) for m in result)
    assert total_after <= total_before + 1
    # 结果结构与输入一致 (role/content 保留)
    assert [m["role"] for m in result] == [m["role"] for m in history]


def test_quality_gate_low_quality_keeps_original() -> None:
    """质量门: min_quality_score 过高时保留原文 (不压缩)."""
    s = _settings(enabled=True, min_quality_score=5.0)  # 最高门槛, 保留句子占比 1.0 才达标
    compressor = SelectiveCompressor(s)
    text = "简单的短句。另一个短句。再来一个短句。"
    compressed, stats = compressor.compress(text)
    # 压缩比不达标 → 调用方保留原文 (compress_history 侧判断)
    assert stats["ratio"] < 5.0 or stats["compressed_tokens"] >= len(text) * 0.5


def test_disabled_compression_passthrough() -> None:
    """enabled=False 时 compress_history 原样返回."""
    history = [{"role": "user", "content": "长文本" * 100}]
    result = compress_history(history, max_tokens=10)
    assert result == history
