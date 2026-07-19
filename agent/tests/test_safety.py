"""安全过滤模块单元测试（AC 自动机）"""

from __future__ import annotations

from smartcs.shared.safety import SafetyFilter, _normalize

# ── 归一化 ──


def test_normalize_fullwidth():
    """全角字符归一化为半角"""
    assert _normalize("ＡＢＣ123") == "abc123"
    assert _normalize("信用卡") == "信用卡"  # 中文不受影响


def test_normalize_lowercase():
    """大写转小写"""
    assert _normalize("HelloWorld") == "helloworld"


# ── AC 自动机匹配 ──


def test_check_input_hit():
    """检测命中敏感词"""
    sf = SafetyFilter()
    sf.load_from_set({"保本保息", "保证收益", "零风险"})

    is_safe, hits = sf.check_input("这个产品保本保息吗？")
    assert not is_safe
    assert "保本保息" in hits


def test_check_input_no_hit():
    """无敏感词时返回安全"""
    sf = SafetyFilter()
    sf.load_from_set({"保本保息"})

    is_safe, hits = sf.check_input("信用卡年费怎么减免")
    assert is_safe
    assert hits == []


def test_check_input_multiple_hits():
    """同时命中多个敏感词"""
    sf = SafetyFilter()
    sf.load_from_set({"保证收益", "零风险", "保本保息"})

    is_safe, hits = sf.check_input("保证收益且零风险的产品")
    assert not is_safe
    assert len(hits) == 2
    assert "保证收益" in hits
    assert "零风险" in hits


def test_check_input_fullwidth_bypass_prevented():
    """全角输入不能绕过检测"""
    sf = SafetyFilter()
    sf.load_from_set({"保本保息"})

    # 全角字符输入
    is_safe, hits = sf.check_input("这个产品保本保息吗")
    assert not is_safe
    assert "保本保息" in hits


def test_check_input_empty():
    """空文本安全"""
    sf = SafetyFilter()
    sf.load_from_set({"保本保息"})

    is_safe, hits = sf.check_input("")
    assert is_safe
    assert hits == []


def test_check_input_detailed_with_positions():
    """详细命中信息包含位置"""
    sf = SafetyFilter()
    sf.load_from_set({"保本保息"})

    is_safe, hits = sf.check_input_detailed("产品保本保息推荐")
    assert not is_safe
    assert len(hits) == 1
    assert hits[0]["word"] == "保本保息"
    assert "start" in hits[0]
    assert "end" in hits[0]


# ── 输出过滤 ──


def test_filter_output_basic():
    """输出过滤：敏感词替换为掩码"""
    sf = SafetyFilter(mask_char="*")
    sf.load_from_set({"保本保息", "零风险"})

    filtered = sf.filter_output("我们保证保本保息且零风险")
    assert "保本保息" not in filtered
    assert "零风险" not in filtered
    assert "***" in filtered


def test_filter_output_preserves_context():
    """输出过滤保留上下文"""
    sf = SafetyFilter(mask_char="*")
    sf.load_from_set({"密码"})

    filtered = sf.filter_output("您的密码是123456")
    assert filtered == "您的**是123456"


def test_filter_output_no_hit():
    """无敏感词时原文返回"""
    sf = SafetyFilter()
    sf.load_from_set({"保本保息"})

    assert sf.filter_output("信用卡年费政策") == "信用卡年费政策"


def test_filter_output_mask_length():
    """掩码长度与敏感词长度一致"""
    sf = SafetyFilter(mask_char="*")
    sf.load_from_set({"ABC"})

    filtered = sf.filter_output("xxxABCxxx")
    assert filtered == "xxx***xxx"


# ── 增量添加 ──


def test_add_word_incremental():
    """增量添加敏感词无需全量重建"""
    sf = SafetyFilter()
    sf.load_from_set({"保本保息"})

    is_safe, _ = sf.check_input("零风险")
    assert is_safe  # 还没有这个词

    sf.add_word("零风险")
    is_safe, hits = sf.check_input("零风险")
    assert not is_safe
    assert "零风险" in hits


# ── 文件加载 ──


def test_load_from_file(tmp_path):
    """从文件加载敏感词"""
    words_file = tmp_path / "words.txt"
    words_file.write_text("# 注释\n保本保息\n零风险\n\n# 另一个注释\n保证收益\n", encoding="utf-8")

    sf = SafetyFilter()
    count = sf.load_from_file(str(words_file))
    assert count == 3
    assert sf.word_count == 3

    is_safe, hits = sf.check_input("保本保息推荐")
    assert not is_safe
    assert "保本保息" in hits


def test_load_from_file_not_exists():
    """文件不存在时返回 0"""
    sf = SafetyFilter()
    count = sf.load_from_file("/nonexistent/path/words.txt")
    assert count == 0


# ── 大规模性能 ──


def test_large_word_list_performance():
    """10000 词库匹配性能 < 100ms"""
    import time

    sf = SafetyFilter()
    # 生成 10000 个假敏感词
    words = {f"敏感词{i:05d}" for i in range(10000)}
    sf.load_from_set(words)

    text = "这是一段包含敏感词00042和敏感词09999的测试文本" * 100

    start = time.monotonic()
    is_safe, hits = sf.check_input(text)
    elapsed_ms = (time.monotonic() - start) * 1000

    assert not is_safe
    assert len(hits) == 2
    assert elapsed_ms < 100, f"AC 匹配耗时 {elapsed_ms:.1f}ms，应 < 100ms"
