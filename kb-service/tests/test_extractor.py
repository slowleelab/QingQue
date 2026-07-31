"""Extractor 测试 — LLM 响应解析纯函数路径

只测 _parse_response (3 种输入格式), 不调真实 LLM.
"""

from __future__ import annotations

import pytest

# extractor 通过 kb.logging -> structlog, structlog 在 poetry install 时未自动装上
pytest.importorskip("structlog", reason="kb-service 要求 Python 3.11 (structlog 在 3.14/缺 lock 时未自动解析)")

from kb.pipeline.extractor import LLMExtractor  # noqa: E402


class TestParseResponse:
    def test_bare_json(self):
        text = '{"keywords": ["年费", "金卡"], "summary": "金卡年费政策", "entities": [], "faq_pairs": []}'
        result = LLMExtractor._parse_response(text)
        assert result.keywords == ["年费", "金卡"]
        assert result.summary == "金卡年费政策"
        assert result.entities == []
        assert result.faq_pairs == []

    def test_markdown_wrapped_json(self):
        text = """```json
{
  "keywords": ["额度", "提额"],
  "summary": "提升额度的几种方式",
  "entities": [{"type": "金额", "value": "5万"}],
  "faq_pairs": [{"question": "如何提额?", "answer": "多用卡"}]
}
```"""
        result = LLMExtractor._parse_response(text)
        assert result.keywords == ["额度", "提额"]
        assert result.summary == "提升额度的几种方式"
        assert len(result.entities) == 1
        assert result.entities[0]["value"] == "5万"
        assert len(result.faq_pairs) == 1
        assert result.faq_pairs[0]["question"] == "如何提额?"

    def test_markdown_wrapped_no_language_tag(self):
        text = """```
{"keywords": ["挂失"], "summary": "挂失流程", "entities": [], "faq_pairs": []}
```"""
        result = LLMExtractor._parse_response(text)
        assert result.keywords == ["挂失"]

    def test_garbage_returns_empty(self):
        # 完全无 JSON, 不应抛错, 降级为空结果
        result = LLMExtractor._parse_response("对不起, 我无法处理这个请求")
        assert result.keywords == []
        assert result.summary == ""
        assert result.entities == []
        assert result.faq_pairs == []

    def test_partial_json_recovered(self):
        # LLM 偶尔在 JSON 前后说废话, 应能截取 {...} 部分
        text = '好的, 抽取结果如下: {"keywords": ["分期"], "summary": "x", "entities": [], "faq_pairs": []} 请查收'
        result = LLMExtractor._parse_response(text)
        assert result.keywords == ["分期"]
        assert result.summary == "x"

    def test_broken_json_returns_empty(self):
        # JSON 损坏 -> 截取后仍解析失败 -> 空结果
        text = '{"keywords": ["a", "b", '
        result = LLMExtractor._parse_response(text)
        assert result.keywords == []

    def test_missing_fields_default_to_empty(self):
        # LLM 可能漏字段, 应有默认值
        text = '{"keywords": ["k1"]}'
        result = LLMExtractor._parse_response(text)
        assert result.keywords == ["k1"]
        assert result.summary == ""
        assert result.entities == []
        assert result.faq_pairs == []

    def test_empty_string(self):
        result = LLMExtractor._parse_response("")
        assert result.keywords == []
