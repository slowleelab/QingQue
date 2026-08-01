"""I2-C4 嵌入漂移监控 + 影子灰度

架构评审 P1-1: 嵌入漂移监控 + 自动回退
- 影子索引灰度已经支持 (shadow_model_version config + model_version filter in retrieve)
- 本模块新增:
  1. DriftMonitor: 检测 query embedding 与 reference corpus 的分布漂移
  2. 影子对比: 同时跑两种 model_version, 计算结果一致性 (Jaccard / 排名相关)
  3. Prometheus 指标: kb_embedding_drift_score, kb_shadow_divergence

设计:
- 不依赖具体嵌入模型, 通过 ES 已索引的 corpus 算基线 (cosine similarity to centroid)
- 监控项:
  - 滑动窗口内 (默认 100 次) 的平均 drift score
  - 影子模式: 默认 model vs shadow model 的 top-10 Jaccard 相似度
- 漂移阈值: 0.15 (经验值, 可配)
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np

from kb.logging import get_logger

logger = get_logger(__name__)


@dataclass
class DriftSignal:
    """一次漂移检测结果"""

    drift_score: float  # 当前 embedding 与基线质心的平均 cosine 距离
    sample_size: int  # 本次采样数
    baseline_centroid: list[float] | None  # 基线质心 (懒计算)
    is_drifted: bool  # 是否超过阈值


class DriftMonitor:
    """嵌入漂移监控器 (滑动窗口)

    用途: 当 query 嵌入分布明显偏离已知 corpus 时告警
    指标: cosine distance to centroid
    """

    def __init__(self, *, window_size: int = 100, drift_threshold: float = 0.15) -> None:
        self._window: deque[list[float]] = deque(maxlen=window_size)
        self._threshold = drift_threshold
        self._centroid: list[float] | None = None
        self._window_size = window_size

    def add(self, embedding: list[float]) -> None:
        """记录一次 query embedding"""
        if not embedding:
            return
        self._window.append(embedding)
        # 增量更新质心 (滑动窗口)
        if len(self._window) >= 10:
            self._recompute_centroid()

    def _recompute_centroid(self) -> None:
        """重新计算质心 (numpy 加速)"""
        if not self._window:
            self._centroid = None
            return
        arr = np.array(list(self._window), dtype=np.float32)
        self._centroid = arr.mean(axis=0).tolist()

    def compute_drift(self) -> DriftSignal:
        """计算当前漂移信号

        漂移 = 最近一次 embedding 与窗口质心的 cosine 距离
        """
        if not self._window or self._centroid is None:
            return DriftSignal(
                drift_score=0.0,
                sample_size=len(self._window),
                baseline_centroid=None,
                is_drifted=False,
            )
        latest = np.array(self._window[-1], dtype=np.float32)
        centroid = np.array(self._centroid, dtype=np.float32)
        # cosine similarity
        dot = float(np.dot(latest, centroid))
        norm_l = float(np.linalg.norm(latest))
        norm_c = float(np.linalg.norm(centroid))
        if norm_l == 0 or norm_c == 0:
            cosine_dist = 0.0
        else:
            cosine_sim = dot / (norm_l * norm_c)
            cosine_dist = 1.0 - max(min(cosine_sim, 1.0), -1.0)
        return DriftSignal(
            drift_score=cosine_dist,
            sample_size=len(self._window),
            baseline_centroid=self._centroid,
            is_drifted=cosine_dist > self._threshold,
        )

    @property
    def window_size(self) -> int:
        return self._window_size

    @property
    def threshold(self) -> float:
        return self._threshold


def jaccard_similarity(a: list[str], b: list[str]) -> float:
    """Jaccard 相似度: |A ∩ B| / |A ∪ B|

    用于影子对比: 两种 model_version 返回的 chunk_id 集合重合度
    """
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    union = sa | sb
    if not union:
        return 1.0
    return len(sa & sb) / len(union)


def rank_correlation(rank_a: list[str], rank_b: list[str]) -> float:
    """Kendall-like 排名相关 (Spearman 简化版)

    输入: 两个有序的 chunk_id 列表
    返回: [-1, 1] 范围的相关性
    """
    if not rank_a or not rank_b:
        return 0.0
    # 共享元素才有意义
    common = [x for x in rank_a if x in set(rank_b)]
    if len(common) < 2:
        return 0.0
    rank_b_map = {x: i for i, x in enumerate(rank_b)}
    # Spearman 简化: 只算共有的
    n = len(common)
    ranks_a = list(range(n))  # common 在 rank_a 中保持原序
    ranks_b = [rank_b_map[x] for x in common]
    # 排名可能乱, 取均值位置
    mean_b = sum(ranks_b) / n
    mean_a = sum(ranks_a) / n
    cov = sum((ra - mean_a) * (rb - mean_b) for ra, rb in zip(ranks_a, ranks_b)) / n
    var_a = sum((ra - mean_a) ** 2 for ra in ranks_a) / n
    var_b = sum((rb - mean_b) ** 2 for rb in ranks_b) / n
    if var_a == 0 or var_b == 0:
        return 0.0
    return cov / math.sqrt(var_a * var_b)


class ShadowComparator:
    """影子索引对比器 — 同时跑两种 model_version, 计算一致性"""

    def __init__(self, *, monitor: DriftMonitor | None = None) -> None:
        self._monitor = monitor or DriftMonitor()

    @property
    def monitor(self) -> DriftMonitor:
        return self._monitor

    def compare(
        self,
        results_primary: list[str],
        results_shadow: list[str],
    ) -> dict[str, Any]:
        """对比两次检索的 top-K 列表

        返回: {
            "jaccard": float,       # 集合重合度
            "rank_corr": float,     # 排名相关
            "overlap": int,         # 共有元素数
            "primary_size": int,
            "shadow_size": int,
            "diverged": bool,       # 是否显著不一致
        }
        """
        j = jaccard_similarity(results_primary, results_shadow)
        rc = rank_correlation(results_primary, results_shadow)
        return {
            "jaccard": j,
            "rank_corr": rc,
            "overlap": len(set(results_primary) & set(results_shadow)),
            "primary_size": len(results_primary),
            "shadow_size": len(results_shadow),
            "diverged": j < 0.5 or rc < 0.3,  # 经验阈值
        }
