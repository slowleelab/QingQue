"""I2-C4 嵌入漂移监控 + 影子灰度单元测试

覆盖:
- DriftMonitor 滑动窗口 + 质心更新
- 漂移阈值检测
- jaccard_similarity / rank_correlation 工具函数
- ShadowComparator.compare 完整对比
"""

from __future__ import annotations

import math

import pytest

from kb.retrieval.drift import (
    DriftMonitor,
    ShadowComparator,
    jaccard_similarity,
    rank_correlation,
)


# ── DriftMonitor ──


class TestDriftMonitor:
    """滑动窗口 + 质心 + 漂移检测"""

    def test_initial_no_drift(self):
        monitor = DriftMonitor(window_size=10, drift_threshold=0.15)
        signal = monitor.compute_drift()
        assert signal.drift_score == 0.0
        assert signal.sample_size == 0
        assert signal.is_drifted is False

    def test_recording_increases_sample_size(self):
        monitor = DriftMonitor(window_size=10)
        for i in range(5):
            monitor.add([1.0, 0.0, 0.0])
        assert len(monitor._window) == 5

    def test_window_size_caps(self):
        monitor = DriftMonitor(window_size=3)
        for i in range(10):
            monitor.add([float(i), 0.0, 0.0])
        assert len(monitor._window) == 3

    def test_centroid_calculated_after_threshold(self):
        monitor = DriftMonitor(window_size=10, drift_threshold=0.15)
        for _ in range(15):
            monitor.add([1.0, 0.0, 0.0])
        signal = monitor.compute_drift()
        assert signal.baseline_centroid is not None
        # 同一向量与质心 cosine 距离 = 0
        assert signal.drift_score < 0.01

    def test_drift_detected_when_orthogonal(self):
        """正交向量 → cosine 距离 ≈ 1, 触发漂移"""
        monitor = DriftMonitor(window_size=20, drift_threshold=0.15)
        # 先灌 10 个 [1, 0, 0] 方向
        for _ in range(10):
            monitor.add([1.0, 0.0, 0.0])
        # 加一个 [0, 1, 0] 方向 — 与质心正交
        monitor.add([0.0, 1.0, 0.0])
        signal = monitor.compute_drift()
        assert signal.drift_score > 0.5
        assert signal.is_drifted is True

    def test_no_drift_for_similar_vectors(self):
        """相似向量 → 低漂移"""
        monitor = DriftMonitor(window_size=20, drift_threshold=0.5)
        for i in range(15):
            # 都在 [1, 0.1, 0] 附近
            monitor.add([1.0, 0.1 + i * 0.001, 0.0])
        signal = monitor.compute_drift()
        assert signal.drift_score < 0.3
        assert signal.is_drifted is False

    def test_empty_embedding_ignored(self):
        monitor = DriftMonitor(window_size=10)
        monitor.add([])
        assert len(monitor._window) == 0

    def test_zero_vector_does_not_crash(self):
        monitor = DriftMonitor(window_size=10)
        monitor.add([0.0, 0.0, 0.0])
        monitor.add([1.0, 0.0, 0.0])
        signal = monitor.compute_drift()
        # 零向量不影响计算
        assert isinstance(signal.drift_score, float)


# ── Jaccard / Rank Correlation ──


class TestJaccardSimilarity:
    def test_identical_sets_full_similarity(self):
        assert jaccard_similarity(["a", "b", "c"], ["a", "b", "c"]) == 1.0

    def test_disjoint_sets_zero_similarity(self):
        assert jaccard_similarity(["a", "b"], ["c", "d"]) == 0.0

    def test_partial_overlap(self):
        s = jaccard_similarity(["a", "b", "c"], ["b", "c", "d"])
        # 共有 {b,c} (2), 并集 {a,b,c,d} (4) → 0.5
        assert abs(s - 0.5) < 1e-9

    def test_both_empty_returns_one(self):
        assert jaccard_similarity([], []) == 1.0

    def test_one_empty_returns_zero(self):
        assert jaccard_similarity([], ["a"]) == 0.0


class TestRankCorrelation:
    def test_identical_ranking_perfect_correlation(self):
        rc = rank_correlation(["a", "b", "c"], ["a", "b", "c"])
        assert rc == 1.0

    def test_reversed_ranking_negative_correlation(self):
        rc = rank_correlation(["a", "b", "c"], ["c", "b", "a"])
        assert rc < 0

    def test_empty_returns_zero(self):
        assert rank_correlation([], []) == 0.0
        assert rank_correlation(["a"], []) == 0.0

    def test_single_common_element(self):
        # 只有 1 个共同元素, n < 2 → 0
        assert rank_correlation(["a", "b"], ["a", "c"]) == 0.0


# ── ShadowComparator ──


class TestShadowComparator:
    def setup_method(self):
        self.monitor = DriftMonitor()
        self.comparator = ShadowComparator(monitor=self.monitor)

    def test_identical_results_not_diverged(self):
        result = self.comparator.compare(["a", "b", "c"], ["a", "b", "c"])
        assert result["jaccard"] == 1.0
        assert result["diverged"] is False
        assert result["overlap"] == 3

    def test_completely_different_diverged(self):
        result = self.comparator.compare(["a", "b"], ["x", "y"])
        assert result["jaccard"] == 0.0
        assert result["diverged"] is True

    def test_partial_overlap(self):
        result = self.comparator.compare(["a", "b", "c", "d"], ["b", "c", "e", "f"])
        assert result["overlap"] == 2
        assert 0 < result["jaccard"] < 1
        assert result["primary_size"] == 4
        assert result["shadow_size"] == 4

    def test_uses_monitor_when_provided(self):
        comparator = ShadowComparator(monitor=self.monitor)
        assert comparator.monitor is self.monitor

    def test_default_creates_monitor(self):
        comparator = ShadowComparator()
        assert comparator.monitor is not None
        assert isinstance(comparator.monitor, DriftMonitor)
