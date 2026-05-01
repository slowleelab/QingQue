"""RAG 评估指标

实现 MRR、Hit@K、NDCG@K、Precision@K 等检索评估指标。
"""

from __future__ import annotations

import math


def mrr(relevances: list[list[bool]]) -> float:
    """Mean Reciprocal Rank

    计算所有查询的第一个相关结果的排名倒数的均值。

    Args:
        relevances: 外层 list 每个元素是一个 query 的 top-K 结果相关性布尔列表

    Returns:
        MRR 值 (0.0 ~ 1.0)
    """
    if not relevances:
        return 0.0

    total = 0.0
    for rel_list in relevances:
        for i, is_relevant in enumerate(rel_list):
            if is_relevant:
                total += 1.0 / (i + 1)
                break
    return total / len(relevances)


def hit_at_k(relevances: list[list[bool]], k: int) -> float:
    """Hit@K: 前 K 个结果中至少有一个相关的查询比例

    Args:
        relevances: 外层 list 每个元素是一个 query 的 top-K 结果相关性布尔列表
        k: 截断位置

    Returns:
        Hit@K 值 (0.0 ~ 1.0)
    """
    if not relevances:
        return 0.0

    hits = 0
    for rel_list in relevances:
        if any(rel_list[:k]):
            hits += 1
    return hits / len(relevances)


def ndcg_at_k(relevances: list[list[bool]], k: int) -> float:
    """Normalized Discounted Cumulative Gain at K

    Args:
        relevances: 外层 list 每个元素是一个 query 的 top-K 结果相关性布尔列表
        k: 截断位置

    Returns:
        NDCG@K 值 (0.0 ~ 1.0)
    """
    if not relevances:
        return 0.0

    total = 0.0
    for rel_list in relevances:
        # DCG
        dcg = 0.0
        for i, is_relevant in enumerate(rel_list[:k]):
            if is_relevant:
                dcg += 1.0 / math.log2(i + 2)  # i+2 because rank starts at 1

        # Ideal DCG: 所有相关文档排在最前面
        num_relevant = sum(rel_list)
        idcg = 0.0
        for i in range(min(num_relevant, k)):
            idcg += 1.0 / math.log2(i + 2)

        if idcg > 0:
            total += dcg / idcg

    return total / len(relevances)


def precision_at_k(relevances: list[list[bool]], k: int) -> float:
    """Precision@K: 前 K 个结果中相关文档的比例均值

    Args:
        relevances: 外层 list 每个元素是一个 query 的 top-K 结果相关性布尔列表
        k: 截断位置

    Returns:
        P@K 值 (0.0 ~ 1.0)
    """
    if not relevances:
        return 0.0

    total = 0.0
    for rel_list in relevances:
        total += sum(rel_list[:k]) / k
    return total / len(relevances)
