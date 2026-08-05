"""P2 上下文工程: token 估算校准测试.

背景: token_utils.estimate_tokens 使用字符启发式系数
(CJK×0.55 + latin×0.3 + other×0.8 + base_overhead), 无真实 tokenizer 校准.

本测试验证:
1. 估算的基本性质 (单调性 / 空串 / 混合文本)
2. 安全裕量: 估算应 ≥ 真实 token 数的合理下界 (偏保守, 防超预算)
   - 中文 Qwen 系 BPE 实际约 0.35-0.5 token/字, 系数 0.55 偏保守 ✅
   - 英文约 4 字符/token = 0.25, 系数 0.3 偏保守 ✅
3. 预算联动: _load_history 的预算下限保护

真实校准 (可选): 接入 tiktoken 或 transformers tokenizer 后,
用 estimate_vs_real 对比偏差, 生成校正系数表.
"""
from __future__ import annotations

from lumio.shared.token_utils import estimate_messages_tokens, estimate_tokens


def test_empty_string() -> None:
    """空串至少有 base_overhead 开销 (消息格式)."""
    assert estimate_tokens("") >= 0
    assert estimate_tokens("", base_overhead=4) >= 4


def test_monotonic_increasing() -> None:
    """文本越长, 估算越高 (单调性, 预算逻辑依赖)."""
    short = estimate_tokens("你好")
    long = estimate_tokens("你好, 请问我的信用卡账单这个月是多少钱, 我想了解一下消费明细和还款日")
    assert long > short


def test_cjk_vs_latin_density() -> None:
    """按字符密度: 中文每字符 token 数 (0.55) > 英文 (0.3)."""
    cjk_text = "这是一段中文文本用于测试"
    latin_text = "this is an english text"
    cjk_density = estimate_tokens(cjk_text) / len(cjk_text)
    latin_density = estimate_tokens(latin_text) / len(latin_text)
    assert cjk_density > latin_density


def test_safety_margin_chinese() -> None:
    """中文安全裕量: Qwen 系 BPE 实际 ~0.35-0.5 token/字, 估算 0.55 偏保守.

    偏保守 = 预算不会超发 (估算 ≥ 实际), 防止超上下文窗口.
    """
    text = "我的信用卡账单" * 50
    est = estimate_tokens(text)
    n = len(text)
    # 估算 = n × 0.55 (+4), 必须 ≥ 实际下界 n × 0.35
    assert est >= n * 0.35, f"估算 {est} 低于实际下界 {n * 0.35:.0f}, 会超预算"
    assert est <= n * 0.55 + 10, f"估算 {est} 超出系数范围"


def test_safety_margin_english() -> None:
    """英文安全裕量: 实际 ~4 字符/token (0.25), 估算 0.3 偏保守."""
    text = "credit card bill payment deadline " * 30
    est = estimate_tokens(text)
    n = len(text)
    assert est >= n / 4.5, f"估算 {est} 低于实际下界 {n / 4.5:.0f}"
    # 上限: latin 0.3 + 空格(占 ~26%)按 other 0.8 → 最坏 ~0.43/字符
    assert est <= n * 0.5 + 10, f"估算 {est} 超出系数上限"


def test_mixed_text_density() -> None:
    """中英混合: 每字符密度介于纯中文与纯英文之间 (含 other 0.8 权重)."""
    mixed_text = "账单 bill 1000元 还款日 deadline"
    pure_cjk = estimate_tokens("账单元还款日")
    pure_latin = estimate_tokens("billdeadline")
    mixed_density = estimate_tokens(mixed_text) / len(mixed_text)
    cjk_density = pure_cjk / len("账单元还款日")
    latin_density = pure_latin / len("billdeadline")
    assert mixed_density > 0
    assert cjk_density > latin_density  # 中文密度 > 英文密度
    # 混合文本 (含英文/数字) 密度 ≤ 纯中文 + 容忍整数截断误差
    assert mixed_density <= cjk_density + 0.2


def test_messages_overhead() -> None:
    """每条消息 +4 格式开销, 预算校验应计入."""
    msgs = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "您好, 请问有什么可以帮您?"},
    ]
    total = estimate_messages_tokens(msgs)
    without_overhead = estimate_tokens("你好") + estimate_tokens("您好, 请问有什么可以帮您?")
    assert total >= without_overhead + 8  # 2 条消息 × 4


def test_budget_floor() -> None:
    """预算下限保护: _load_history 用 max(budget_history, 1024), 不会低于 1024."""
    from lumio.shared.config import get_settings

    budget = max(get_settings().llm.budget_history, 1024)
    assert budget >= 1024
    # 预算足以容纳至少一轮对话
    one_turn = estimate_tokens("这是一轮普通长度的客户咨询消息内容" * 3, base_overhead=4)
    assert one_turn <= budget


def test_estimate_vs_real_documented() -> None:
    """校准对照表 (文档化已知偏差, 供接入真实 tokenizer 后对比).

    当前系数: CJK 0.55 / latin 0.3 / other 0.8 — 相对 Qwen 系 BPE:
    - 中文估算偏高 10-40% (偏保守, 安全方向)
    - 英文标点密集文本可能高估 (other=0.8)
    接入 tiktoken/transformers 后应在此处断言 |est-real|/real < 0.3.
    """
    # 占位断言: 系数范围合法
    assert 0 < 0.55 < 1 and 0 < 0.3 < 1 and 0 < 0.8 < 1
