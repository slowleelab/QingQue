"""I3-C2 SLO 告警 (4 档 burn-rate)

架构评审 P1-3 配套: 检索超时降级 + SLO 告警

Google SRE 多窗口 burn-rate 模式:
- 长期 (1h) 慢烧 — 整体 error budget 消耗
- 中期 (6h) 中烧 — 跨班次
- 短期 (1h) 快烧 — 突增
- 即时 (5m) 速烧 — 立即响应

4 档告警阈值 (标准 SRE 实践):
- 1h burn > 14.4× (2% 在 1h 内烧完 1% error budget)
- 6h burn > 6× (5% 在 6h 内烧完)
- 1h burn > 3× (10% 慢烧)
- 1d burn > 1× (持续超 SLO)

本模块:
- SLO 定义 (availability, latency_p95, latency_p99)
- BurnRate 计算 (基于滑动窗口)
- AlertEvaluator: 给定当前 metrics, 返回触发的告警
- 生成 Prometheus alerting rules (YAML 输出)

设计:
- 不依赖 Prometheus 服务, 提供纯函数计算
- 配套 admin 端点 /api/v1/admin/slo 实时查询
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SLI(str, Enum):
    """SLI 类型"""

    AVAILABILITY = "availability"  # 可用性
    LATENCY_P95 = "latency_p95"  # P95 延迟
    LATENCY_P99 = "latency_p99"  # P99 延迟


@dataclass
class SLOTarget:
    """单个 SLO 目标

    P1-3.1 字段语义修正:
    - target: 达标率 (如 0.99 = 99% 在阈值内), 与 SLI 类型无关的"通用"含义
    - latency_threshold_s: 仅 LATENCY_P95/P99 使用, 配合 target 表示
      "target 比例的请求必须在 latency_threshold_s 秒内返回"
    """

    name: str
    sli: SLI
    target: float  # 达标率 (0.99 = 99%)
    window: str = "30d"  # 评估窗口
    description: str = ""
    latency_threshold_s: float = 0.0  # 仅 LATENCY SLO 有效; 0 = 未配置


# ── 4 档 burn-rate 阈值 (Google SRE workbook 标准) ──

# (window_minutes, threshold_multiplier)
# 阈值 = 1 / (1 - target) / window_factor, 这里简化为查表
BURN_RATE_THRESHOLDS = {
    # page-level: 5min 速烧, 阈值高
    "page": [
        (5, 14.4),  # 5min × 14.4× → 2% error budget/h
    ],
    # ticket-level: 30min 快烧
    "ticket": [
        (30, 6.0),  # 30min × 6× → 5% budget/6h
    ],
    # warning-level: 6h 中烧
    "warning": [
        # 6h × 3× → 5% budget/24h
        (360, 3.0),
    ],
    # info-level: 24h 慢烧
    "info": [
        # 24h × 1× → 持续超 SLO
        (1440, 1.0),
    ],
}


@dataclass
class BurnRateSnapshot:
    """一次 burn-rate 快照"""

    slo_name: str
    sli: SLI
    window_minutes: int
    error_count: int
    total_count: int
    burn_rate: float  # 实际 burn rate (与 target 比较)
    threshold: float  # 该窗口告警阈值
    triggered: bool  # 是否触发
    severity: str = ""  # page / ticket / warning / info


def compute_burn_rate(
    *,
    target: float,
    error_count: int,
    total_count: int,
    window_minutes: int,
    budget_window_minutes: int = 30 * 24 * 60,  # 30 天
) -> float:
    """计算 burn rate (Google SRE 多窗口)

    burn_rate = (error_rate / (1 - target)) × (budget_window / window)

    - error_rate: 当前窗口错误率
    - (1 - target): 允许的错误率
    - ratio: 当前消耗速率 (1.0 = 正好按 SLO 烧)
    - 标准化: 乘以 budget_window/window 表示"如果按此速率持续, 多久烧完 budget"
      短窗口同样的 error_rate 会得到更高的 burn_rate (意味着快速消耗)

    - burn_rate=1.0: 当前速率下, 正好在 budget 周期内烧完 budget
    - burn_rate=2.0: 在 half budget 周期烧完
    - burn_rate=14.4: 5min 内烧完 (page-level 告警)

    返回: 0.0 表示无错误或无效输入
    """
    if total_count <= 0:
        return 0.0
    if target >= 1.0:
        return 0.0
    if window_minutes <= 0:
        return 0.0
    error_rate = error_count / total_count
    allowed_error_rate = 1.0 - target
    if allowed_error_rate <= 0:
        return 0.0
    return (error_rate / allowed_error_rate) * (budget_window_minutes / window_minutes)


class AlertEvaluator:
    """SLO 告警评估器

    接收 metrics, 输出当前所有触发的告警.
    """

    def __init__(self, slos: list[SLOTarget]) -> None:
        self._slos = slos

    @property
    def slos(self) -> list[SLOTarget]:
        return list(self._slos)

    def evaluate(
        self,
        *,
        slo_name: str,
        sli: SLI,
        error_count: int,
        total_count: int,
    ) -> list[BurnRateSnapshot]:
        """评估一个 SLO 的所有 4 档窗口

        返回触发的告警 (threshold = 实际阈值, burn_rate 超过则 triggered)
        """
        # 找 SLO
        slo = next((s for s in self._slos if s.name == slo_name and s.sli == sli), None)
        if slo is None:
            return []

        snapshots: list[BurnRateSnapshot] = []
        for severity, configs in BURN_RATE_THRESHOLDS.items():
            for window_min, threshold in configs:
                burn = compute_burn_rate(
                    target=slo.target,
                    error_count=error_count,
                    total_count=total_count,
                    window_minutes=window_min,
                )
                snapshots.append(
                    BurnRateSnapshot(
                        slo_name=slo_name,
                        sli=sli,
                        window_minutes=window_min,
                        error_count=error_count,
                        total_count=total_count,
                        burn_rate=burn,
                        threshold=threshold,
                        triggered=burn > threshold,
                        severity=severity,
                    )
                )
        return snapshots

    def all_active_alerts(
        self,
        metrics: dict[tuple[str, SLI], tuple[int, int]],
    ) -> list[BurnRateSnapshot]:
        """批量评估多个 SLO

        metrics: {(slo_name, sli): (error_count, total_count)}
        """
        alerts: list[BurnRateSnapshot] = []
        for (slo_name, sli), (err, total) in metrics.items():
            snaps = self.evaluate(slo_name=slo_name, sli=sli, error_count=err, total_count=total)
            alerts.extend(s for s in snaps if s.triggered)
        return alerts


# ── 默认 SLO 集合 ──


DEFAULT_SLOS: list[SLOTarget] = [
    SLOTarget(
        name="retrieve_availability",
        sli=SLI.AVAILABILITY,
        target=0.99,  # 99% 可用
        window="30d",
        description="检索 API 可用性 SLO 99% (30 天窗口)",
    ),
    SLOTarget(
        name="retrieve_p95_latency",
        sli=SLI.LATENCY_P95,
        target=0.95,  # 95% 请求在阈值内
        window="30d",
        latency_threshold_s=1.5,  # 1.5s
        description="检索 P95 延迟 SLO: 95% 请求在 1.5s 内返回",
    ),
    SLOTarget(
        name="retrieve_p99_latency",
        sli=SLI.LATENCY_P99,
        target=0.99,  # 99% 在阈值内
        window="30d",
        latency_threshold_s=2.0,  # 2s
        description="检索 P99 延迟 SLO: 99% 请求在 2s 内返回",
    ),
]


# ── Prometheus rules 生成 ──


def _build_availability_error_rate(window_min: int) -> str:
    """availability 错误率子表达式 (rate 分子/分母)

    P1-3.1: 对齐 prometheus.py:41-45 实际指标 kb_retrieve_total{status="failed"}
    (旧实现误用 kb_retrieve_errors_total, 实际不存在)
    """
    return (
        f'sum(rate(kb_retrieve_total{{status="failed"}}[{window_min}m])) / sum(rate(kb_retrieve_total[{window_min}m]))'
    )


def _build_availability_promql(window_min: int, target: float) -> str:
    """完整 availability 告警表达式 (错误率 > 1 - target)

    保留供外部直接调用 (单条 SLO 单条规则); 主路径在 generate_prometheus_rules 用 _build_availability_error_rate
    """
    return f"{_build_availability_error_rate(window_min)} > (1 - {target})"


def _build_latency_promql(window_min: int, target: float, threshold_s: float) -> str:
    """latency SLO PromQL: histogram_quantile(percentile) > threshold

    P1-3.1: LATENCY 用 histogram_quantile (与 availability 不同):
    - target (0.95) → quantile=0.95
    - latency_threshold_s (1.5) → 实际秒数阈值
    """
    quantile = target  # 0.95/0.99 直接作为分位数
    return (
        f"histogram_quantile({quantile}, "
        f"sum by (le) (rate(kb_retrieve_duration_seconds_bucket[{window_min}m]))) "
        f"> {threshold_s}"
    )


def generate_prometheus_rules(slos: list[SLOTarget] | None = None) -> str:
    """生成 Prometheus alerting rules YAML

    输出格式: 标准 Prometheus rule_files
    P1-3.1: 按 SLI 类型分支 — availability / latency 用不同 PromQL
    """
    import yaml

    if slos is None:
        slos = DEFAULT_SLOS

    groups = []
    for slo in slos:
        rules = []
        for severity, configs in BURN_RATE_THRESHOLDS.items():
            for window_min, threshold in configs:
                # 按 SLI 类型选择 PromQL
                if slo.sli == SLI.AVAILABILITY:
                    # availability: 标准 burn rate 比较
                    #   error_rate / (1 - target) = 当前消耗速率
                    #   > threshold (1×/3×/6×/14.4×) = 触发对应档
                    error_rate = _build_availability_error_rate(window_min)
                    expr = f"({error_rate}) / (1 - {slo.target}) > {threshold}"
                    summary_suffix = f"error rate {severity} 超阈值"
                else:  # LATENCY_P95 / LATENCY_P99
                    # latency: 所有窗口统一用 base 阈值, 紧急度靠 for: 区分
                    # 旧实现误用 (1 + (threshold-1)*0.1) 放大阈值, 短窗口反而更宽松 (与 Google SRE 相反)
                    # Google SRE 模式: 短窗口 page 告警的"严格"靠 (a) for: 短, (b) 短窗口采样更敏感,
                    # 不靠放大阈值. 阈值固定 = 真实 SLO 阈值, 才是正确语义.
                    threshold_s = slo.latency_threshold_s
                    if threshold_s <= 0:
                        # 防御: DATACLASS default 是 0, 但 SLO 不应未配就生效
                        continue
                    expr = _build_latency_promql(window_min, slo.target, threshold_s)
                    summary_suffix = f"latency {severity} 超阈值"

                rules.append(
                    {
                        "alert": f"{slo.name}_burn_{severity}",
                        "expr": expr,
                        "for": f"{max(1, window_min // 4)}m",
                        "labels": {
                            "severity": severity,
                            "slo": slo.name,
                            "sli": slo.sli.value,
                        },
                        "annotations": {
                            "summary": f"{slo.name} {summary_suffix}",
                            "description": (
                                f"SLO {slo.name} (target={slo.target}) 在 {window_min}m 窗口内 "
                                f"超过 {threshold}× 配置阈值."
                            ),
                        },
                    }
                )
        groups.append({"name": f"kb_slo_{slo.name}", "rules": rules})

    return yaml.safe_dump({"groups": groups}, allow_unicode=True, sort_keys=False)


# ── Error budget 剩余 ──


@dataclass
class ErrorBudgetStatus:
    """当前 error budget 状态"""

    slo_name: str
    target: float
    total_budget_minutes: float
    consumed_minutes: float
    remaining_minutes: float
    remaining_pct: float
    window_days: int = 30

    @property
    def healthy(self) -> bool:
        return self.remaining_pct > 0.5


def compute_error_budget(
    *,
    slo_name: str = "unknown",
    target: float,
    error_count: int,
    total_count: int,
    window_days: int = 30,
) -> ErrorBudgetStatus:
    """计算 error budget 剩余

    budget = (1 - target) * total_count
    consumed = error_count
    remaining = budget - consumed

    P1-3.1: slo_name 字段外部填充 (旧实现永远是 "unknown", 是 P0-4 评审指出的语义 bug)
    """
    if total_count <= 0 or target >= 1.0:
        return ErrorBudgetStatus(
            slo_name=slo_name,
            target=target,
            total_budget_minutes=0.0,
            consumed_minutes=0.0,
            remaining_minutes=0.0,
            remaining_pct=1.0,
            window_days=window_days,
        )

    error_rate = error_count / total_count
    allowed = 1.0 - target
    # 用分钟做单位: 假设均匀分布
    total_budget = allowed * total_count
    consumed = error_count
    remaining = max(0.0, total_budget - consumed)
    remaining_pct = remaining / total_budget if total_budget > 0 else 1.0

    return ErrorBudgetStatus(
        slo_name=slo_name,
        target=target,
        total_budget_minutes=total_budget,
        consumed_minutes=consumed,
        remaining_minutes=remaining,
        remaining_pct=remaining_pct,
        window_days=window_days,
    )
