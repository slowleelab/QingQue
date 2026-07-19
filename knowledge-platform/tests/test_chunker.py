"""分块器测试"""

from app.pipeline.chunker import chunk_by_structure, chunk_text, ChunkType


def test_chunk_text_short():
    """短文本不分块"""
    text = "这是一段短文本。"
    result = chunk_text(text, chunk_size=1500, overlap=200)
    assert len(result) == 1
    assert result[0] == text


def test_chunk_text_long():
    """长文本按句号分块"""
    text = "第一句话。" * 500  # 约 1500 字
    result = chunk_text(text, chunk_size=500, overlap=50)
    assert len(result) > 1
    # 每块不应超过 chunk_size + 搜索窗口
    for chunk in result:
        assert len(chunk) <= 700  # 500 + 200 搜索窗口


def test_chunk_by_structure_markdown():
    """Markdown 结构感知分块"""
    text = """## 第一节

这是第一节的内容，包含足够长的文本以确保分块器正常工作。

## 第二节

这是第二节的内容，同样包含足够长的文本。
"""
    result = chunk_by_structure(text, source_type="MARKDOWN", max_chunk_size=1500)
    assert len(result) >= 2
    assert result[0].chunk_type in (ChunkType.SECTION, ChunkType.PLAIN_TEXT)


def test_chunk_by_structure_faq():
    """FAQ 文档分块"""
    text = """## 信用卡年费怎么收？

年费标准为金卡200元，白金卡1000元。

## 积分怎么兑换？

可以在APP内兑换商品。
"""
    result = chunk_by_structure(text, source_type="MARKDOWN", doc_type="faq", max_chunk_size=1500)
    assert len(result) >= 2
    for chunk in result:
        assert chunk.chunk_type == ChunkType.FAQ_QA


def test_chunk_by_structure_plain_text():
    """非 Markdown 回退递归字符分块"""
    text = "这是一段纯文本。" * 200
    result = chunk_by_structure(text, source_type="TXT", max_chunk_size=500)
    assert len(result) > 1
    assert all(c.chunk_type == ChunkType.PLAIN_TEXT for c in result)
