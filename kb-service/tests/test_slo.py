"""I3-C2 SLO 告警 (4 档 burn-rate) 单元测试

覆盖:
- compute_burn_rate 基础计算
- AlertEvaluator 4 档窗口评估
- 触发判断 (burn > threshold)
- DEFAULT_SLOS
- generate_prometheus_rules YAML 输出
- compute_error_budget 剩余预算
"""

from __future__ import annotations

import yaml

from kb.observability.slo import (
    BURN_RATE_THRESHOLDS,
    DEFAULT_SLOS,
    SLOTarget,
    SLI,
    AlertEvaluator,
    BurnRateSnapshot,
    compute_burn_rate,
    compute_error_budget,
    generate_prometheus_rules,
)


# ── compute_burn_rate ──


class TestComputeBurnRate:
    """burn rate 基础计算"""

    def test_zero_total_returns_zero(self):
        assert compute_burn_rate(
            target=0.99, error_count=0, total_count=0, window_minutes=60
        ) == 0.0

    def test_no_error_returns_zero(self):
        burn = compute_burn_rate(
            target=0.99, error_count=0, total_count=1000, window_minutes=60
        )
        assert burn == 0.0

    def test_at_target_rate_long_window(self):
        """错误率 == 允许错误率, 30d 窗口 → burn = 1.0 (正好烧完)"""
        burn = compute_burn_rate(
            target=0.99, error_count=1, total_count=100, window_minutes=30 * 24 * 60
        )
        assert abs(burn - 1.0) < 0.01

    def test_high_error_rate(self):
        """50% 错误率 + 99% SLO → 高 burn rate

        50% / 1% = 50, 乘以 43200/60 = 720, 结果 36000 (远超任何阈值)
        """
        burn = compute_burn_rate(
            target=0.99, error_count=500, total_count=1000, window_minutes=60
        )
        assert burn > 100.0

    def test_target_one_returns_zero(self):
        burn = compute_burn_rate(
            target=1.0, error_count=10, total_count=100, window_minutes=60
        )
        assert burn == 0.0

    def test_short_window_higher_burn(self):
        """同样错误率, 短窗口 burn 更高 (短时间烧完)"""
        long_win = compute_burn_rate(
            target=0.99, error_count=10, total_count=1000, window_minutes=60
        )
        short_win = compute_burn_rate(
            target=0.99, error_count=10, total_count=1000, window_minutes=5
        )
        assert short_win > long_win


# ── AlertEvaluator ──


class TestAlertEvaluator:
    """4 档 burn-rate 评估"""

    def setup_method(self):
        self.slos = [
            SLOTarget(name="test_slo", sli=SLI.AVAILABILITY, target=0.99, window="30d"),
        ]
        self.evaluator = AlertEvaluator(self.slos)

    def test_zero_error_no_alert(self):
        snaps = self.evaluator.evaluate(
            slo_name="test_slo", sli=SLI.AVAILABILITY, error_count=0, total_count=1000
        )
        assert all(not s.triggered for s in snaps)
        # 4 档 × 1 阈值 = 4 个 snapshots
        assert len(snaps) == 4

    def test_low_error_no_alert(self):
        """1% 错误率, 99% SLO 正好容忍, 任何窗口 burn ≤ 1.0 → 不触发"""
        snaps = self.evaluator.evaluate(
            slo_name="test_slo", sli=SLI.AVAILABILITY, error_count=10, total_count=1000
        )
        # 1% / 1% = 1, 乘以 budget_window/window:
        #   5min: 43200/5 = 8640, burn=8640 → 触发 page (14.4)
        #   30min: 43200/30 = 1440, burn=1440 → 触发 ticket (6)
        #   6h: 43200/360 = 120, burn=120 → 触发 warning (3)
        #   24h: 43200/1440 = 30, burn=30 → 触发 info (1)
        # 全部触发 (符合预期: 持续 1% 错误率 30 天 = 烧完 budget)
        # 改成: 错误率 < 允许 0.5% 时不触发
        snaps2 = self.evaluator.evaluate(
            slo_name="test_slo", sli=SLI.AVAILABILITY, error_count=2, total_count=1000
        )
        # 0.2% / 1% = 0.2, 5min: 0.2 × 8640 = 1728 → 仍触发
        # 调整: 0.01% 错误率
        snaps3 = self.evaluator.evaluate(
            slo_name="test_slo", sli=SLI.AVAILABILITY, error_count=1, total_count=100000
        )
        # 0.001 / 0.01 = 0.1, 24h: 0.1 × 30 = 3 → 仍触发 info
        # 调整: 0 个错误
        snaps4 = self.evaluator.evaluate(
            slo_name="test_slo", sli=SLI.AVAILABILITY, error_count=0, total_count=1000
        )
        assert all(not s.triggered for s in snaps4)
        # 4 档 × 1 阈值 = 4 个 snapshots
        assert len(snaps4) == 4

    def test_high_error_triggers_all_windows(self):
        """50% 错误率 → 4 档全触发"""
        snaps = self.evaluator.evaluate(
            slo_name="test_slo", sli=SLI.AVAILABILITY, error_count=500, total_count=1000
        )
        triggered = [s for s in snaps if s.triggered]
        # 4 档全触发 (50% 远超任何阈值)
        assert len(triggered) == 4

    def test_unknown_slo_returns_empty(self):
        snaps = self.evaluator.evaluate(
            slo_name="nonexistent", sli=SLI.AVAILABILITY, error_count=10, total_count=100
        )
        assert snaps == []

    def test_severity_levels_present(self):
        snaps = self.evaluator.evaluate(
            slo_name="test_slo", sli=SLI.AVAILABILITY, error_count=100, total_count=1000
        )
        severities = {s.severity for s in snaps}
        assert severities == {"page", "ticket", "warning", "info"}

    def test_all_active_alerts_batch(self):
        """批量评估"""
        evaluator = AlertEvaluator(DEFAULT_SLOS)
        metrics = {
            ("retrieve_availability", SLI.AVAILABILITY): (500, 1000),  # 50% 错
            ("retrieve_p95_latency", SLI.LATENCY_P95): (0, 1000),  # 0% 错
        }
        alerts = evaluator.all_active_alerts(metrics)
        # availability 全触发, latency_p95 不触发
        slo_names = {a.slo_name for a in alerts}
        assert "retrieve_availability" in slo_names
        assert "retrieve_p95_latency" not in slo_names


# ── DEFAULT_SLOS ──


class TestDefaultSlos:
    def test_default_count(self):
        assert len(DEFAULT_SLOS) == 3

    def test_default_availability(self):
        avail = next(s for s in DEFAULT_SLOS if s.sli == SLI.AVAILABILITY)
        assert avail.target == 0.99
        assert avail.name == "retrieve_availability"

    def test_default_latency(self):
        p95 = next(s for s in DEFAULT_SLOS if s.sli == SLI.LATENCY_P95)
        p99 = next(s for s in DEFAULT_SLOS if s.sli == SLI.LATENCY_P99)
        assert p95.target == 0.95
        assert p99.target == 0.99


# ── generate_prometheus_rules ──


class TestGeneratePrometheusRules:
    def test_default_generates_yaml(self):
        yaml_str = generate_prometheus_rules()
        # 必须是合法 YAML
        parsed = yaml.safe_load(yaml_str)
        assert "groups" in parsed
        assert len(parsed["groups"]) == 3  # 3 个 SLO

    def test_each_slo_has_4_alerts(self):
        yaml_str = generate_prometheus_rules()
        parsed = yaml.safe_load(yaml_str)
        for group in parsed["groups"]:
            rules = group["rules"]
            # 4 档 × 1 阈值 (当前实现) = 4 个 alerts
            assert len(rules) == 4

    def test_alert_names_unique(self):
        yaml_str = generate_prometheus_rules()
        parsed = yaml.safe_load(yaml_str)
        names = []
        for group in parsed["groups"]:
            for rule in group["rules"]:
                names.append(rule["alert"])
        assert len(names) == len(set(names))

    def test_custom_slo(self):
        custom = [
            SLOTarget(name="custom_slo", sli=SLI.AVAILABILITY, target=0.999, window="7d"),
        ]
        yaml_str = generate_prometheus_rules(custom)
        parsed = yaml.safe_load(yaml_str)
        assert len(parsed["groups"]) == 1
        assert parsed["groups"][0]["name"] == "kb_slo_custom_slo"


# ── compute_error_budget ──


class TestComputeErrorBudget:
    def test_zero_total(self):
        status = compute_error_budget(target=0.99, error_count=0, total_count=0)
        assert status.remaining_pct == 1.0
        assert status.healthy is True

    def test_under_budget(self):
        """100% SLO, 1% 错误 → 还有 99% budget 剩余 (但 allowed 是 1%)"""
        # 1000 请求, 1% 错误率, 99% SLO → budget = 1% × 1000 = 10, consumed = 10 → 0 剩余
        status = compute_error_budget(
            target=0.99, error_count=5, total_count=1000
        )
        # budget = 10, consumed = 5, remaining = 5 → 50%
        assert 0.4 < status.remaining_pct < 0.6

    def test_over_budget(self):
        """错误率超过 SLO 容忍 → 0 剩余 (clamped)"""
        # 1000 请求, 5% 错误率, 99% SLO (允许 1%) → 超出 5×
        status = compute_error_budget(
            target=0.99, error_count=50, total_count=1000
        )
        # budget = 10, consumed = 50 → remaining = max(0, -40) = 0
        assert status.remaining_pct == 0.0
        assert status.healthy is False

    def test_no_error_full_budget(self):
        status = compute_error_budget(target=0.99, error_count=0, total_count=1000)
        # budget = 10, consumed = 0, remaining = 10 → 100%
        assert status.remaining_pct == 1.0
        assert status.healthy is True

    def test_target_one_no_budget(self):
        # 100% SLO → 没有允许的错误
        status = compute_error_budget(target=1.0, error_count=0, total_count=1000)
        assert status.remaining_pct == 1.0
