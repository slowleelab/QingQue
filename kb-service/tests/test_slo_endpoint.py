"""P1-3.3 /api/v1/admin/slo 端点测试

覆盖:
- slo_metrics.py: 读 counter / histogram
- admin_slo 端点: 鉴权 / 告警 / budget / prometheus rules
- ObservabilitySettings 配置
- 0 指标时降级行为
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

httpx = pytest.importorskip("httpx")
fastapi = pytest.importorskip("fastapi")


# ── slo_metrics.py 单元测试 ──


class TestExtractCounterValue:
    """P1-3.3: counter samples 累加 + label 过滤"""

    def test_sum_all_samples(self):
        from kb.observability.slo_metrics import _extract_counter_value

        # 模拟 prometheus MetricWrapperBase.samples
        s1 = MagicMock()
        s1.name = "kb_retrieve_total"
        s1.labels = {"search_type": "hybrid", "status": "success"}
        s1.value = 100.0
        s2 = MagicMock()
        s2.name = "kb_retrieve_total"
        s2.labels = {"search_type": "hybrid", "status": "failed"}
        s2.value = 5.0
        s3 = MagicMock()
        s3.name = "kb_retrieve_total_created"
        s3.labels = {}
        s3.value = 0.0  # 应当被忽略
        metric = MagicMock()
        metric.samples = [s1, s2, s3]

        assert _extract_counter_value(metric) == 105.0

    def test_label_filter_failed(self):
        from kb.observability.slo_metrics import _extract_counter_value

        s1 = MagicMock()
        s1.name = "kb_retrieve_total"
        s1.labels = {"status": "success"}
        s1.value = 100.0
        s2 = MagicMock()
        s2.name = "kb_retrieve_total"
        s2.labels = {"status": "failed"}
        s2.value = 5.0
        metric = MagicMock()
        metric.samples = [s1, s2]
        assert _extract_counter_value(metric, {"status": "failed"}) == 5.0


class TestExtractHistogramQuantile:
    """P1-3.3: histogram bucket 算 p-quantile"""

    def test_no_count_returns_none(self):
        from kb.observability.slo_metrics import _extract_histogram_quantiles

        metric = MagicMock()
        metric.samples = []
        result = _extract_histogram_quantiles(metric, (0.95, 0.99))
        assert result == {0.95: None, 0.99: None}

    def test_basic_p95(self):
        from kb.observability.slo_metrics import _extract_histogram_quantiles

        # 模拟 100 个请求, 95% 在 1.0s 以内
        s_count = MagicMock()
        s_count.name = "kb_retrieve_duration_seconds_count"
        s_count.value = 100.0
        s_count.labels = {}
        s_b1 = MagicMock()
        s_b1.name = "kb_retrieve_duration_seconds_bucket"
        s_b1.labels = {"le": "0.5"}
        s_b1.value = 50.0
        s_b2 = MagicMock()
        s_b2.name = "kb_retrieve_duration_seconds_bucket"
        s_b2.labels = {"le": "1.0"}
        s_b2.value = 95.0
        s_b3 = MagicMock()
        s_b3.name = "kb_retrieve_duration_seconds_bucket"
        s_b3.labels = {"le": "+Inf"}
        s_b3.value = 100.0

        metric = MagicMock()
        metric.samples = [s_count, s_b1, s_b2, s_b3]
        # p95 = 95/100 = 0.95, 落在 [0.5, 1.0] 区间
        result = _extract_histogram_quantiles(metric, (0.95, 0.99))
        assert result[0.95] is not None
        assert abs(result[0.95] - 1.0) < 0.01

    def test_p99_above_buckets(self):
        from kb.observability.slo_metrics import _extract_histogram_quantiles

        s_count = MagicMock()
        s_count.name = "kb_retrieve_duration_seconds_count"
        s_count.value = 100.0
        s_count.labels = {}
        s_b1 = MagicMock()
        s_b1.name = "kb_retrieve_duration_seconds_bucket"
        s_b1.labels = {"le": "1.0"}
        s_b1.value = 99.0
        s_b2 = MagicMock()
        s_b2.name = "kb_retrieve_duration_seconds_bucket"
        s_b2.labels = {"le": "+Inf"}
        s_b2.value = 100.0
        metric = MagicMock()
        metric.samples = [s_count, s_b1, s_b2]
        # p99 = 99, 落在 [1.0, +Inf] 区间, 返回 prev_upper=1.0
        result = _extract_histogram_quantiles(metric, (0.95, 0.99))
        assert result[0.99] is not None
        assert abs(result[0.99] - 1.0) < 0.01


class TestReadSLOMetrics:
    """P1-3.3: read_slo_metrics 主流程"""

    def test_empty_registry(self):
        from kb.observability.slo_metrics import read_slo_metrics

        fake = MagicMock()
        fake.collect.return_value = []
        metrics, status = read_slo_metrics(fake)
        assert metrics == {}
        assert status == {"counter": False, "histogram": False}

    def test_returns_availability_when_counter_present(self):
        from kb.observability.slo_metrics import read_slo_metrics

        s_ok = MagicMock()
        s_ok.name = "kb_retrieve_total"
        s_ok.labels = {"search_type": "hybrid", "status": "success"}
        s_ok.value = 100.0
        s_fail = MagicMock()
        s_fail.name = "kb_retrieve_total"
        s_fail.labels = {"search_type": "hybrid", "status": "failed"}
        s_fail.value = 5.0
        s_other = MagicMock()
        s_other.name = "kb_retrieve_total"
        s_other.labels = {"search_type": "hybrid", "status": "degraded"}
        s_other.value = 3.0
        metric = MagicMock()
        metric.name = "kb_retrieve_total"
        metric.samples = [s_ok, s_fail, s_other]
        fake = MagicMock()
        fake.collect.return_value = [metric]

        result, status = read_slo_metrics(fake)
        assert status["counter"] is True
        assert status["histogram"] is False
        avail = result[("retrieve_availability", "availability")]
        assert avail.error_count == 5.0
        assert avail.total_count == 108.0

    def test_returns_latency_when_histogram_present(self):
        from kb.observability.slo_metrics import read_slo_metrics

        s_count = MagicMock()
        s_count.name = "kb_retrieve_duration_seconds_count"
        s_count.value = 100.0
        s_count.labels = {}
        s_b1 = MagicMock()
        s_b1.name = "kb_retrieve_duration_seconds_bucket"
        s_b1.labels = {"le": "1.5"}
        s_b1.value = 95.0
        s_b2 = MagicMock()
        s_b2.name = "kb_retrieve_duration_seconds_bucket"
        s_b2.labels = {"le": "+Inf"}
        s_b2.value = 100.0
        metric = MagicMock()
        metric.name = "kb_retrieve_duration_seconds"
        metric.samples = [s_count, s_b1, s_b2]
        fake = MagicMock()
        fake.collect.return_value = [metric]

        result, status = read_slo_metrics(fake)
        assert status["histogram"] is True
        p95 = result[("retrieve_p95_latency", "latency_p95")]
        assert p95.p95_latency is not None
        assert abs(p95.p95_latency - 1.5) < 0.01


# ── /api/v1/admin/slo 端点 ──


class TestAdminSLOEndpoint:
    """P1-3.3: 端点鉴权 / 响应结构"""

    @pytest.mark.asyncio
    async def test_no_metrics_returns_empty_slos(self):
        """无指标时降级: slos=[], active_alerts=[], 但 prometheus_rules_yaml 仍生成"""
        from kb.api.admin import admin_slo

        with patch("kb.config.get_settings") as mock_settings:
            mock_settings.return_value.observability.burn_rate_enabled = True
            mock_settings.return_value.observability.retrieve_p95_threshold_s = 1.5
            mock_settings.return_value.observability.retrieve_p99_threshold_s = 2.0
            with patch("kb.observability.slo_metrics.read_slo_metrics", return_value=({}, {"counter": False, "histogram": False})):
                result = await admin_slo(principal=MagicMock(actor_id="test", tenant_id="default", roles=["admin"]))
        assert result["burn_rate_enabled"] is True
        assert result["slos"] == []
        assert result["active_alerts"] == []
        assert result["error_budgets"] == []
        assert "groups" in result["prometheus_rules_yaml"]

    @pytest.mark.asyncio
    async def test_high_error_rate_triggers_page(self):
        """50% 错误率 / 1% SLO → 4 档全触发 (含 page)"""
        from kb.api.admin import admin_slo
        from kb.observability.slo import SLI
        from kb.observability.slo_metrics import SLOMetrics

        fake_metrics = {
            ("retrieve_availability", SLI.AVAILABILITY.value): SLOMetrics(
                slo_name="retrieve_availability",
                sli=SLI.AVAILABILITY.value,
                error_count=500.0,
                total_count=1000.0,
            )
        }
        with patch("kb.config.get_settings") as mock_settings:
            mock_settings.return_value.observability.burn_rate_enabled = True
            mock_settings.return_value.observability.retrieve_p95_threshold_s = 1.5
            mock_settings.return_value.observability.retrieve_p99_threshold_s = 2.0
            with patch("kb.observability.slo_metrics.read_slo_metrics", return_value=(fake_metrics, {"counter": True, "histogram": True})):
                result = await admin_slo(principal=MagicMock(actor_id="test", tenant_id="default", roles=["admin"]))

        assert len(result["active_alerts"]) == 4  # page/ticket/warning/info 全触发
        severities = {a["severity"] for a in result["active_alerts"]}
        assert severities == {"page", "ticket", "warning", "info"}
        assert all(a["slo"] == "retrieve_availability" for a in result["active_alerts"])

    @pytest.mark.asyncio
    async def test_low_error_no_alert(self):
        """1% 错误率, 99% SLO → burn = 1.0 = budget 用完但未超阈值 (取决于窗口)
        注: 0 错误必然 0 告警"""
        from kb.api.admin import admin_slo
        from kb.observability.slo import SLI
        from kb.observability.slo_metrics import SLOMetrics

        fake_metrics = {
            ("retrieve_availability", SLI.AVAILABILITY.value): SLOMetrics(
                slo_name="retrieve_availability",
                sli=SLI.AVAILABILITY.value,
                error_count=0.0,
                total_count=10000.0,
            )
        }
        with patch("kb.config.get_settings") as mock_settings:
            mock_settings.return_value.observability.burn_rate_enabled = True
            mock_settings.return_value.observability.retrieve_p95_threshold_s = 1.5
            mock_settings.return_value.observability.retrieve_p99_threshold_s = 2.0
            with patch("kb.observability.slo_metrics.read_slo_metrics", return_value=(fake_metrics, {"counter": True, "histogram": True})):
                result = await admin_slo(principal=MagicMock(actor_id="test", tenant_id="default", roles=["admin"]))

        assert result["active_alerts"] == []

    @pytest.mark.asyncio
    async def test_error_budget_consumed(self):
        """50% 错误率 → budget 烧完 (allowed=1%, 实际 50%, 超 50×)"""
        from kb.api.admin import admin_slo
        from kb.observability.slo import SLI
        from kb.observability.slo_metrics import SLOMetrics

        fake_metrics = {
            ("retrieve_availability", SLI.AVAILABILITY.value): SLOMetrics(
                slo_name="retrieve_availability",
                sli=SLI.AVAILABILITY.value,
                error_count=50.0,
                total_count=100.0,  # 50% 错误率
            )
        }
        with patch("kb.config.get_settings") as mock_settings:
            mock_settings.return_value.observability.burn_rate_enabled = True
            mock_settings.return_value.observability.retrieve_p95_threshold_s = 1.5
            mock_settings.return_value.observability.retrieve_p99_threshold_s = 2.0
            with patch("kb.observability.slo_metrics.read_slo_metrics", return_value=(fake_metrics, {"counter": True, "histogram": True})):
                result = await admin_slo(principal=MagicMock(actor_id="test", tenant_id="default", roles=["admin"]))

        # 100 请求, 1% 允许 → budget=1, 消耗 50 → 0 剩余
        assert len(result["error_budgets"]) == 1
        b = result["error_budgets"][0]
        assert b["remaining_pct"] == 0.0
        assert b["healthy"] is False
        assert b["slo"] == "retrieve_availability"

    @pytest.mark.asyncio
    async def test_burn_rate_disabled_no_alerts(self):
        """KB_OBSERVABILITY_BURN_RATE_ENABLED=false → 跳过告警评估"""
        from kb.api.admin import admin_slo
        from kb.observability.slo import SLI
        from kb.observability.slo_metrics import SLOMetrics

        fake_metrics = {
            ("retrieve_availability", SLI.AVAILABILITY.value): SLOMetrics(
                slo_name="retrieve_availability",
                sli=SLI.AVAILABILITY.value,
                error_count=500.0,
                total_count=1000.0,
            )
        }
        with patch("kb.config.get_settings") as mock_settings:
            mock_settings.return_value.observability.burn_rate_enabled = False
            mock_settings.return_value.observability.retrieve_p95_threshold_s = 1.5
            mock_settings.return_value.observability.retrieve_p99_threshold_s = 2.0
            with patch("kb.observability.slo_metrics.read_slo_metrics", return_value=(fake_metrics, {"counter": True, "histogram": True})):
                result = await admin_slo(principal=MagicMock(actor_id="test", tenant_id="default", roles=["admin"]))

        assert result["burn_rate_enabled"] is False
        assert result["active_alerts"] == []  # 关闭时不计算

    @pytest.mark.asyncio
    async def test_prometheus_rules_yaml_present(self):
        """端点返回的 prometheus_rules_yaml 必须是合法 YAML 含 3 个 SLO group"""
        import yaml

        from kb.api.admin import admin_slo

        with patch("kb.config.get_settings") as mock_settings:
            mock_settings.return_value.observability.burn_rate_enabled = True
            mock_settings.return_value.observability.retrieve_p95_threshold_s = 1.5
            mock_settings.return_value.observability.retrieve_p99_threshold_s = 2.0
            with patch("kb.observability.slo_metrics.read_slo_metrics", return_value=({}, {"counter": False, "histogram": False})):
                result = await admin_slo(principal=MagicMock(actor_id="test", tenant_id="default", roles=["admin"]))

        parsed = yaml.safe_load(result["prometheus_rules_yaml"])
        assert len(parsed["groups"]) == 3  # 3 个 SLO

    @pytest.mark.asyncio
    async def test_latency_threshold_overridden_by_settings(self):
        """端点用 ObservabilitySettings.retrieve_p95_threshold_s 覆盖 DEFAULT_SLOS 阈值"""
        from kb.api.admin import admin_slo

        with patch("kb.config.get_settings") as mock_settings:
            mock_settings.return_value.observability.burn_rate_enabled = True
            mock_settings.return_value.observability.retrieve_p95_threshold_s = 3.0  # 自定义
            mock_settings.return_value.observability.retrieve_p99_threshold_s = 5.0
            with patch("kb.observability.slo_metrics.read_slo_metrics", return_value=({}, {"counter": False, "histogram": False})):
                result = await admin_slo(principal=MagicMock(actor_id="test", tenant_id="default", roles=["admin"]))

        # 检查 prometheus_rules_yaml 用了 3.0 / 5.0 而非默认 1.5 / 2.0
        yaml_str = result["prometheus_rules_yaml"]
        # P95 group 中应出现 3.0 阈值 (经放大)
        assert "3." in yaml_str


# ── ObservabilitySettings 配置 ──


class TestObservabilitySettings:
    """P1-3.3: ObservabilitySettings 配置注入"""

    def test_defaults(self):
        from kb.config import ObservabilitySettings

        s = ObservabilitySettings()
        assert s.burn_rate_enabled is True
        assert s.retrieve_p95_threshold_s == 1.5
        assert s.retrieve_p99_threshold_s == 2.0

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch):
        from kb.config import ObservabilitySettings

        monkeypatch.setenv("KB_OBSERVABILITY_BURN_RATE_ENABLED", "false")
        monkeypatch.setenv("KB_OBSERVABILITY_RETRIEVE_P95_THRESHOLD_S", "2.5")
        s = ObservabilitySettings()
        assert s.burn_rate_enabled is False
        assert s.retrieve_p95_threshold_s == 2.5

    def test_settings_wires_observability(self):
        """全局 Settings 包含 observability 子配置"""
        from kb.config import Settings

        s = Settings()
        assert s.observability.burn_rate_enabled is True
        assert s.observability.retrieve_p95_threshold_s == 1.5


# ── P1-3.4: 删除 embedding-drift stub 端点 ──


class TestEmbeddingDriftStubRemoved:
    """P1-3.4: 验证 stub 端点已删除, embedding-drift-live 仍可用"""

    def test_stub_endpoint_gone(self):
        """stub 端点必须从 router 列表中消失 (不再注册)"""
        from kb.api.admin import router as admin_router

        paths = {route.path for route in admin_router.routes}
        # 旧 stub 路径不应存在
        assert "/api/v1/admin/embedding-drift" not in paths
        # 正确的 live 端点必须存在
        assert "/api/v1/admin/embedding-drift-live" in paths

    def test_no_mock_response_in_admin(self):
        """admin.py 中不应再出现返回 mock 数据的代码 (sample_size: 0 占位)"""
        from pathlib import Path

        admin_path = Path(__file__).parent.parent / "kb" / "api" / "admin.py"
        source = admin_path.read_text(encoding="utf-8")
        # stub 端点返回的占位字符串不应再出现
        assert "通过 GET /api/v1/admin/embedding-drift-live 实时查询" not in source

    def test_drift_live_endpoint_intact(self):
        """live 端点保留完整逻辑 (sample_size / drift_score 字段)"""
        from kb.api.admin import embedding_drift_live

        # 函数仍可调用, 不抛
        # 完整逻辑验证由 test_drift.py 覆盖
        assert callable(embedding_drift_live)
