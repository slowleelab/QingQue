"""检索引擎测试"""

from app.retrieval.engine import build_es_filters, _build_cache_key, _date_to_epoch


def test_build_es_filters_keyword():
    """测试 keyword 字段过滤"""
    filters = {"category": "年费", "doc_type": "faq"}
    clauses = build_es_filters(filters)
    assert len(clauses) == 2
    assert {"term": {"category": "年费"}} in clauses
    assert {"term": {"doc_type": "faq"}} in clauses


def test_build_es_filters_date_range():
    """测试日期范围过滤"""
    filters = {"effective_date": {"gte": "2024-01-01", "lte": "2024-12-31"}}
    clauses = build_es_filters(filters)
    assert len(clauses) == 1
    range_clause = clauses[0]["range"]["effective_date"]
    assert "gte" in range_clause
    assert "lte" in range_clause


def test_build_es_filters_keywords_list():
    """测试关键词列表过滤"""
    filters = {"keywords": ["年费", "减免"]}
    clauses = build_es_filters(filters)
    assert len(clauses) == 1
    assert clauses[0] == {"terms": {"keywords": ["年费", "减免"]}}


def test_build_es_filters_none_values():
    """测试 None 值跳过"""
    filters = {"category": "年费", "doc_type": None}
    clauses = build_es_filters(filters)
    assert len(clauses) == 1


def test_cache_key_consistency():
    """测试缓存 key 一致性"""
    key1 = _build_cache_key("信用卡年费", {"category": "年费"}, "hybrid")
    key2 = _build_cache_key("信用卡年费", {"category": "年费"}, "hybrid")
    key3 = _build_cache_key("信用卡年费", {"category": "积分"}, "hybrid")
    assert key1 == key2
    assert key1 != key3


def test_date_to_epoch():
    """测试日期转 epoch"""
    epoch = _date_to_epoch("2024-01-01")
    assert epoch > 0
    assert _date_to_epoch("invalid") == 0
    assert _date_to_epoch("") == 0
