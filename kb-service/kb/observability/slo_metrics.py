"""P1-3.3: 从 Prometheus 指标读 SLO 当前状态 (给 /api/v1/admin/slo 端点用)

设计: 直接读 in-process prometheus_client REGISTRY, 避免 HTTP 循环调用 /metrics.
- availability: 累加 RETRIEVE_COUNT{status="failed"} 与 sum(RETRIEVE_COUNT{status=*})
- latency: 从 RETRIEVE_LATENCY histogram buckets 算 p95/p99 (类似 histogram_quantile)

C3 注意: 这只读 in-process counter, 与生产 Prometheus 服务器采集的指标值是一致的
(同一进程), 不引入时钟偏移.

C3 局限: 不分租户 — 多租户环境返回全平台 SLO. 端点 docstring 显式标注.
要分租户需给 Counter/Histogram 加 tenant_id label (大改, 留 P1-2).

M3 fix: 每次返回 _registry_status 标记 counter / histogram 是否已注册,
便于 cold-start 告警诊断.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from time import time
from typing import Any, Protocol

from prometheus_client import REGISTRY
from prometheus_client.metrics_core import Metric

from kb.observability.slo import SLI


class _HasCollect(Protocol):
    """最小化类型: 任何有 .collect() 方法的对象 (REGISTRY / CollectorRegistry)"""

    def collect(self) -> Any: ...


@dataclass
class SLOMetrics:
    """单个 SLO 当前指标读数"""

    slo_name: str
    sli: str
    error_count: float = 0.0
    total_count: float = 0.0
    p95_latency: float | None = None
    p99_latency: float | None = None


# 检索 counter / histogram 名称 (与 kb/middleware/prometheus.py 对齐)
_RETRIEVE_COUNTER = "kb_retrieve_total"
_RETRIEVE_LATENCY = "kb_retrieve_duration_seconds"


def _extract_counter_value(metric: Metric, label_filter: dict[str, str] | None = None) -> float:
    """累加一个 Counter 的所有 samples, 可选按 label 过滤

    Counter 的 samples 结构: [name, name+"_created", ...], 我们只取 name 本身.
    """
    total = 0.0
    for sample in metric.samples:
        # sample.name 是 "kb_retrieve_total" (Counter) 或 "kb_retrieve_total_created"
        if sample.name != _RETRIEVE_COUNTER:
            continue
        if label_filter and not all(sample.labels.get(k) == v for k, v in label_filter.items()):
            continue
        total += sample.value
    return total


def _extract_histogram_quantiles(
    metric: Metric, quantiles: tuple[float, ...] = (0.95, 0.99)
) -> dict[float, float | None]:
    """从 Histogram samples 一次算多个 p-quantile (类似 histogram_quantile())

    H4 fix: 旧实现 p95 + p99 各遍历一次 samples, 重复 IO. 现在一次遍历算所有 quantile.

    Histogram samples 结构:
    - {name}_bucket{le="0.01"}: 累积计数
    - {name}_count: 总数
    - {name}_sum: 总和
    - {name}_created: 元数据

    算法: 找每个 quantile 对应的 bucket 上界, 线性插值.
    """
    buckets: list[tuple[float, float]] = []  # (upper_bound, cumulative_count)
    total_count = 0.0
    for sample in metric.samples:
        if sample.name == f"{_RETRIEVE_LATENCY}_count":
            total_count = sample.value
        elif sample.name == f"{_RETRIEVE_LATENCY}_bucket":
            le_str = sample.labels.get("le")
            if le_str is None:
                continue
            # le="+Inf" 表示无穷大
            if le_str == "+Inf":
                upper = math.inf
            else:
                try:
                    upper = float(le_str)
                except ValueError:
                    continue
            buckets.append((upper, sample.value))
    if total_count <= 0 or not buckets:
        return dict.fromkeys(quantiles, None)
    # 按 upper_bound 排序
    buckets.sort(key=lambda x: x[0])

    # 一次遍历算所有 quantile: 对每个 bucket 边界, 检查哪些 quantile 落在此区间
    # 用 heap 维护 (target_count, quantile) 列表, 每跨一个 bucket 处理已满足的 quantile
    result: dict[float, float | None] = {}
    pending: list[tuple[float, float]] = sorted((q * total_count, q) for q in quantiles)  # 按 target 升序

    prev_upper = 0.0
    prev_count = 0.0
    pending_idx = 0
    for upper, count in buckets:
        # 处理所有 target <= count 的 quantile
        while pending_idx < len(pending) and pending[pending_idx][0] <= count:
            target, q = pending[pending_idx]
            if upper == math.inf:
                result[q] = prev_upper
            elif count == prev_count:
                result[q] = upper
            else:
                fraction = (target - prev_count) / (count - prev_count)
                result[q] = prev_upper + fraction * (upper - prev_upper)
            pending_idx += 1
        prev_upper = upper
        prev_count = count

    # 未触发的 quantile (count 永远 < target) → None
    while pending_idx < len(pending):
        _, q = pending[pending_idx]
        result.setdefault(q, None)
        pending_idx += 1
    return result


# H3 fix: 5s 缓存避免高 QPS 监控轮询放大 REGISTRY.collect() 开销
_CACHE_TTL_S = 5.0
_cache: dict[str, Any] = {}


def read_slo_metrics(
    registry: _HasCollect = REGISTRY,
) -> tuple[dict[tuple[str, str], SLOMetrics], dict[str, bool]]:
    """从 in-process prometheus_client 读数

    返回: (metrics, registry_status)
    - metrics: {(slo_name, sli_value): SLOMetrics}
      slo_name: "retrieve_availability" / "retrieve_p95_latency" / "retrieve_p99_latency"
      sli_value: SLI.value
    - registry_status: {counter: bool, histogram: bool}
      cold-start 诊断: counter / histogram 是否已注册
    """
    # H3 fix: lru_cache 5s TTL, 高 QPS 监控轮询 (10s 一次) 不会反复 collect()
    cache_key = id(registry)  # registry id (默认 REGISTRY 是同一对象)
    now = time()
    if cache_key in _cache and now - _cache[cache_key]["t"] < _CACHE_TTL_S:
        return _cache[cache_key]["metrics"], _cache[cache_key]["status"]

    metrics: dict[tuple[str, str], SLOMetrics] = {}
    counter_found = False
    histogram_found = False
    if not hasattr(registry, "collect"):
        _cache[cache_key] = {
            "t": now,
            "metrics": metrics,
            "status": {"counter": False, "histogram": False},
        }
        return metrics, {"counter": False, "histogram": False}

    for metric in registry.collect():
        if metric.name == _RETRIEVE_COUNTER:
            counter_found = True
            failed = _extract_counter_value(metric, {"status": "failed"})
            total = _extract_counter_value(metric)
            metrics[("retrieve_availability", SLI.AVAILABILITY.value)] = SLOMetrics(
                slo_name="retrieve_availability",
                sli=SLI.AVAILABILITY.value,
                error_count=failed,
                total_count=total,
            )
        elif metric.name == _RETRIEVE_LATENCY:
            histogram_found = True
            # H4 fix: 一次遍历算 p95 + p99
            quantiles = _extract_histogram_quantiles(metric, (0.95, 0.99))
            p95 = quantiles.get(0.95)
            p99 = quantiles.get(0.99)
            # latency SLO 用 p95/p99 作为"成功阈值", error_count=0
            # (latency SLO 的"错误"是延迟 > 阈值, 这里只暴露原始 quantile 不下结论)
            if p95 is not None:
                metrics[("retrieve_p95_latency", SLI.LATENCY_P95.value)] = SLOMetrics(
                    slo_name="retrieve_p95_latency",
                    sli=SLI.LATENCY_P95.value,
                    error_count=0.0,
                    total_count=0.0,
                    p95_latency=p95,
                )
            if p99 is not None:
                metrics[("retrieve_p99_latency", SLI.LATENCY_P99.value)] = SLOMetrics(
                    slo_name="retrieve_p99_latency",
                    sli=SLI.LATENCY_P99.value,
                    error_count=0.0,
                    total_count=0.0,
                    p99_latency=p99,
                )

    status = {"counter": counter_found, "histogram": histogram_found}
    _cache[cache_key] = {"t": now, "metrics": metrics, "status": status}
    return metrics, status


def clear_slo_cache() -> None:
    """清缓存 (单测用, 避免 cache 跨测试泄漏)"""
    _cache.clear()


# 显式导出 (供单测 mock)
__all__ = [
    "SLOMetrics",
    "_extract_counter_value",
    "_extract_histogram_quantiles",
    "clear_slo_cache",
    "read_slo_metrics",
]
