"""清洗器测试"""

from app.pipeline.cleaner import clean_text


def test_clean_page_header_footer():
    """测试移除页眉页脚"""
    text = "正文内容\n第 1 页/共 3 页\n更多正文\nPage 2 of 5"
    result = clean_text(text)
    assert "第 1 页" not in result
    assert "Page 2 of 5" not in result
    assert "正文内容" in result
    assert "更多正文" in result


def test_clean_control_chars():
    """测试移除控制字符"""
    text = "正常\x00文本\x07\x1f"
    result = clean_text(text)
    assert "\x00" not in result
    assert "\x07" not in result
    assert "\x1f" not in result
    assert "正常" in result
    assert "文本" in result


def test_clean_multi_spaces():
    """测试折叠连续空格"""
    text = "多个    空格    在这里"
    result = clean_text(text)
    assert "    " not in result


def test_clean_multi_newlines():
    """测试折叠多余换行"""
    text = "段落一\n\n\n\n\n段落二"
    result = clean_text(text)
    assert "\n\n\n" not in result


def test_clean_dedup():
    """测试段落去重"""
    text = "重复段落\n重复段落\n唯一段落"
    result = clean_text(text)
    # 第二个"重复段落"应被去重
    assert result.count("重复段落") == 1
    assert "唯一段落" in result
