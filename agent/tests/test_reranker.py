"""重排序服务单元测试 (reranker.py)"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lumio.services.common.reranker import (
    OllamaReranker,
    TEIReranker,
    create_reranker_provider,
)
from lumio.shared.models import RerankResult

# ── _parse_score ──


def test_parse_score_normal():
    """正常浮点分数"""
    assert OllamaReranker._parse_score("0.85") == 0.85
    assert OllamaReranker._parse_score("相关性: 0.7") == 0.7


def test_parse_score_clamp():
    """超出 0~1 范围被钳制 (负号被正则丢弃, 取绝对值)"""
    assert OllamaReranker._parse_score("2.5") == 1.0
    assert OllamaReranker._parse_score("-0.3") == 0.3


def test_parse_score_no_number():
    """无数字 → 0"""
    assert OllamaReranker._parse_score("无法评分") == 0.0


# ── OllamaReranker ──


def test_ollama_name():
    """name 含模型名"""
    r = OllamaReranker(base_url="http://localhost:11434/", model="bge")
    assert r.base_url == "http://localhost:11434"  # 去尾部斜杠
    assert r.name == "ollama:bge"


def test_ollama_rerank_empty():
    """空文档 → 空结果"""
    assert OllamaReranker().rerank("q", []) == []


@patch("lumio.services.common.reranker.httpx.post")
def test_ollama_rerank_scores_and_sorts(mock_post):
    """并发评分后按分数降序取 top_k"""

    def side_effect(url, json=None, timeout=None):
        resp = MagicMock()
        text = json["prompt"]
        if "文档A" in text:
            resp.json.return_value = {"response": "0.9"}
        else:
            resp.json.return_value = {"response": "0.5"}
        return resp

    mock_post.side_effect = side_effect
    r = OllamaReranker()
    results = r.rerank("查询", ["文档A", "文档B"], top_k=2)
    assert len(results) == 2
    assert results[0].index == 0  # 文档A 得分高排前
    assert results[0].relevance_score == 0.9
    assert isinstance(results[0], RerankResult)


@patch("lumio.services.common.reranker.httpx.post")
def test_ollama_rerank_topk_truncates(mock_post):
    """top_k 截断"""

    def side_effect(url, json=None, timeout=None):
        resp = MagicMock()
        resp.json.return_value = {"response": "0.5"}
        return resp

    mock_post.side_effect = side_effect
    r = OllamaReranker()
    results = r.rerank("q", ["d1", "d2", "d3"], top_k=2)
    assert len(results) == 2


@patch("lumio.services.common.reranker.httpx.post")
def test_ollama_rerank_error_returns_zero(mock_post):
    """评分请求异常 → 该文档 0 分"""
    mock_post.side_effect = RuntimeError("network down")
    r = OllamaReranker()
    results = r.rerank("q", ["d1"])
    assert results[0].relevance_score == 0.0


@patch("lumio.services.common.reranker.httpx.get")
def test_ollama_health_check_ok(mock_get):
    """健康检查: 200 → True"""
    mock_get.return_value = MagicMock(status_code=200)
    assert OllamaReranker().health_check() is True


@patch("lumio.services.common.reranker.httpx.get")
def test_ollama_health_check_fail(mock_get):
    """健康检查: 异常 → False"""
    mock_get.side_effect = RuntimeError("down")
    assert OllamaReranker().health_check() is False


# ── TEIReranker ──


def test_tei_name():
    """name 含模型名"""
    assert TEIReranker(model="bge").name == "tei:bge"


def test_tei_rerank_empty():
    """空文档 → 空结果"""
    assert TEIReranker().rerank("q", []) == []


@patch("lumio.services.common.reranker.httpx.post")
def test_tei_rerank_ok(mock_post):
    """TEI 返回排序结果"""
    mock_post.return_value = MagicMock(
        json=lambda: [
            {"index": 1, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.8},
        ]
    )
    r = TEIReranker()
    results = r.rerank("q", ["d0", "d1"])
    assert len(results) == 2
    assert results[0].index == 1
    assert results[0].text == "d1"  # 按 index 取原文


@patch("lumio.services.common.reranker.httpx.post")
def test_tei_rerank_index_out_of_range(mock_post):
    """index 越界 → 空文本"""
    mock_post.return_value = MagicMock(json=lambda: [{"index": 5, "relevance_score": 0.9}])
    r = TEIReranker()
    results = r.rerank("q", ["d0"])
    assert results[0].text == ""


@patch("lumio.services.common.reranker.httpx.post")
def test_tei_rerank_error_returns_empty(mock_post):
    """请求异常 → 空结果"""
    mock_post.side_effect = RuntimeError("down")
    assert TEIReranker().rerank("q", ["d0"]) == []


@patch("lumio.services.common.reranker.httpx.get")
def test_tei_health_check(mock_get):
    """健康检查"""
    mock_get.return_value = MagicMock(status_code=200)
    assert TEIReranker().health_check() is True
    mock_get.side_effect = RuntimeError("down")
    assert TEIReranker().health_check() is False


# ── 工厂 ──


def test_create_reranker_provider_ollama():
    """工厂: ollama"""
    p = create_reranker_provider(provider_type="ollama")
    assert isinstance(p, OllamaReranker)


def test_create_reranker_provider_tei():
    """工厂: tei"""
    p = create_reranker_provider(provider_type="tei")
    assert isinstance(p, TEIReranker)


def test_create_reranker_provider_invalid():
    """工厂: 未知类型抛 ValueError"""
    with pytest.raises(ValueError, match="不支持"):
        create_reranker_provider(provider_type="weaviate")
