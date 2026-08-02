"""P1-3.3: 从 Prometheus 指标读 SLO 当前状态 (给 /api/v1/admin/slo 端点用)

设计: 直接读 in-process prometheus_client REGISTRY, 避免 HTTP 循环调用 /metrics.
- availability: 累加 RETRIEVE_COUNT{status="failed"} 与 sum(RETRIEVE_COUNT{status=*})
- latency: 从 RETRIEVE_LATENCY histogram buckets 算 p95/p99 (类似 histogram_quantile)

注意: 这只读 in-process counter, 与生产 Prometheus 服务器采集的指标值是一致的
(同一进程), 不引入时钟偏移.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from prometheus_client import REGISTRY
from prometheus_client.metrics import MetricWrapperBase

from kb.observability.slo import SLI


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


def _extract_counter_value(metric: MetricWrapperBase, label_filter: dict[str, str] | None = None) -> float:
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


def _extract_histogram_quantile(metric: MetricWrapperBase, quantile: float) -> float | None:
    """从 Histogram samples 算 p-quantile (类似 Prometheus histogram_quantile())

    Histogram samples 结构:
    - {name}_bucket{le="0.01"}: 累积计数
    - {name}_count: 总数
    - {name}_sum: 总和
    - {name}_created: 元数据

    算法: 找 quantile 对应的 bucket 上界, 线性插值.
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
        return None
    # 按 upper_bound 排序
    buckets.sort(key=lambda x: x[0])
    # 目标累计计数
    target = total_count * quantile
    prev_upper = 0.0
    prev_count = 0.0
    for upper, count in buckets:
        if count >= target:
            # 在 [prev_upper, upper] 区间内线性插值
            if upper == math.inf:
                return prev_upper
            if count == prev_count:
                return upper
            # 假设线性: count - prev_count 映射 upper - prev_upper
            fraction = (target - prev_count) / (count - prev_count)
            return prev_upper + fraction * (upper - prev_upper)
        prev_upper = upper
        prev_count = count
    return None


def read_slo_metrics(
    registry: MetricWrapperBase | object = REGISTRY,
) -> dict[tuple[str, str], SLOMetrics]:
    """从 in-process prometheus_client 读数

    返回: {(slo_name, sli_value): SLOMetrics}
    - slo_name: "retrieve_availability" / "retrieve_p95_latency" / "retrieve_p99_latency"
    - sli_value: SLI.value
    """
    metrics: dict[tuple[str, str], SLOMetrics] = {}
    if not hasattr(registry, "collect"):
        return metrics

    # 遍历所有指标, 累加 counter / histogram
    for metric in registry.collect():  # type: ignore[attr-defined]
        if metric.name == _RETRIEVE_COUNTER:
            failed = _extract_counter_value(metric, {"status": "failed"})
            total = _extract_counter_value(metric)
            metrics[("retrieve_availability", SLI.AVAILABILITY.value)] = SLOMetrics(
                slo_name="retrieve_availability",
                sli=SLI.AVAILABILITY.value,
                error_count=failed,
                total_count=total,
            )
        elif metric.name == _RETRIEVE_LATENCY:
            p95 = _extract_histogram_quantile(metric, 0.95)
            p99 = _extract_histogram_quantile(metric, 0.99)
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
    return metrics


# 显式导出 (供单测 mock)
__all__ = [
    "SLOMetrics",
    "_extract_counter_value",
    "_extract_histogram_quantile",
    "read_slo_metrics",
]
