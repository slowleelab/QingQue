"""RAG 评估指标测试"""
from __future__ import annotations

from scripts.experiments.metrics import hit_at_k, mrr, ndcg_at_k, precision_at_k


class TestMRR:
    def test_perfect(self):
        """全部第一个命中 → MRR = 1.0"""
        relevances = [[True, False], [True, True, False]]
        assert mrr(relevances) == 1.0

    def test_no_hit(self):
        """全部未命中 → MRR = 0.0"""
        relevances = [[False, False], [False, False, False]]
        assert mrr(relevances) == 0.0

    def test_second_hit(self):
        """第二个命中 → MRR = 0.5"""
        relevances = [[False, True]]
        assert mrr(relevances) == 0.5

    def test_empty(self):
        assert mrr([]) == 0.0


class TestHitAtK:
    def test_hit_at_3(self):
        relevances = [[False, False, True], [True, False, False], [False, False, False]]
        assert hit_at_k(relevances, 3) == 2 / 3

    def test_no_hits(self):
        relevances = [[False, False], [False, False]]
        assert hit_at_k(relevances, 5) == 0.0


class TestNDCG:
    def test_perfect(self):
        """完美排序 → NDCG = 1.0"""
        relevances = [[True, True, False], [True, False, False]]
        assert ndcg_at_k(relevances, 3) == 1.0

    def test_empty(self):
        assert ndcg_at_k([], 5) == 0.0


class TestPrecisionAtK:
    def test_precision(self):
        relevances = [[True, True, False, False, False]]
        assert precision_at_k(relevances, 5) == 0.4

    def test_all_relevant(self):
        relevances = [[True, True, True]]
        assert precision_at_k(relevances, 3) == 1.0

    def test_none_relevant(self):
        relevances = [[False, False, False]]
        assert precision_at_k(relevances, 3) == 0.0
