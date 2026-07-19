"""敏感词过滤器测试 — AC 自动机"""

from app.security.sensitive_filter import SensitiveWordFilter


def test_scan_basic():
    """测试基本敏感词扫描"""
    sf = SensitiveWordFilter(words=["盗刷", "套现", "洗钱"])
    hits = sf.scan("客户反映信用卡被盗刷，怀疑有人套现")
    words = [h["word"] for h in hits]
    assert "盗刷" in words
    assert "套现" in words
    assert "洗钱" not in words


def test_scan_no_hit():
    """测试无敏感词"""
    sf = SensitiveWordFilter(words=["盗刷"])
    hits = sf.scan("这是一段正常文本")
    assert len(hits) == 0


def test_contains_sensitive():
    """测试快速判断"""
    sf = SensitiveWordFilter(words=["诈骗"])
    assert sf.contains_sensitive("小心诈骗") is True
    assert sf.contains_sensitive("正常文本") is False


def test_mask():
    """测试脱敏"""
    sf = SensitiveWordFilter(words=["盗刷"])
    masked = sf.mask("信用卡被盗刷了")
    assert "盗刷" not in masked
    assert "**" in masked
    assert "信用卡被" in masked
    assert "了" in masked


def test_overlapping_words():
    """测试重叠匹配"""
    sf = SensitiveWordFilter(words=["信用卡盗刷", "盗刷"])
    hits = sf.scan("发生信用卡盗刷事件")
    words = [h["word"] for h in hits]
    # 两个词都应命中
    assert "信用卡盗刷" in words
    assert "盗刷" in words


def test_empty_text():
    """测试空文本"""
    sf = SensitiveWordFilter(words=["盗刷"])
    assert sf.scan("") == []
    assert sf.contains_sensitive("") is False


def test_reload():
    """测试热替换词典"""
    sf = SensitiveWordFilter(words=["旧词"])
    assert sf.contains_sensitive("旧词") is True
    assert sf.contains_sensitive("新词") is False

    sf.reload(["新词"])
    assert sf.contains_sensitive("旧词") is False
    assert sf.contains_sensitive("新词") is True


def test_default_words():
    """测试默认敏感词库"""
    sf = SensitiveWordFilter()
    assert sf.word_count > 0
    assert sf.contains_sensitive("盗刷") is True


def test_scan_parsed_text_not_raw_bytes():
    """测试对解析后文本扫描（模拟 orchestrator 场景）

    PDF/DOCX 原始 bytes 用 decode 会产生乱码，
    AC 自动机应在解析后的纯文本上扫描才有意义。
    """
    sf = SensitiveWordFilter(words=["盗刷", "套现"])

    # 模拟 Parse 后的纯文本（应在此层扫描）
    parsed_text = "客户反映信用卡被盗刷，怀疑有人套现"
    hits = sf.scan(parsed_text)
    words = [h["word"] for h in hits]
    assert "盗刷" in words
    assert "套现" in words
