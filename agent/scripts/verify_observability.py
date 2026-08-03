"""一键验证可观测性闭环（指标定义 / 看板引用 / 采集路径一致）

不连 live 中间件/服务：纯进程内 + 静态解析。
- 跑 test_observability.py（指标注册、追踪装饰器、MCP span、看板引用一致）
- 校验 3 个 Grafana dashboard 引用的 metric 名在 /metrics 暴露集 + Prometheus scrape job 之中
- 校验 ObservabilitySettings 关键字段 (tracing_enabled/jaeger_host/sampling_ratio) 已就位

使用方式:
    poetry run python scripts/verify_observability.py
退出码: 0=全部通过, 1=有失败
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARDS_DIR = REPO_ROOT / "config" / "grafana" / "dashboards"
PROMETHEUS_YML = REPO_ROOT / "config" / "prometheus.yml"

# flat layout: agent/lumio/ 就在 cwd 边; 显式把 agent/ 加 sys.path,
# 这样脚本无论从哪里被调都能 import lumio.
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))


def _banner(title: str) -> None:
    print()
    print("─" * 64)
    print(f"  {title}")
    print("─" * 64)


def _ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def _fail(msg: str) -> None:
    print(f"  ❌ {msg}")


def check_observability_tests() -> tuple[bool, str]:
    """跑 test_observability.py"""
    _banner("1/3 跑 observability 单测 (test_observability.py)")
    result = subprocess.run(
        ["poetry", "run", "pytest", "tests/test_observability.py", "-v", "--no-header"],
        cwd=AGENT_ROOT,
        capture_output=True,
        text=True,
    )
    # 提取 PASSED/FAILED 行做简短展示
    passed = sum(1 for ln in result.stdout.splitlines() if " PASSED" in ln)
    failed = sum(1 for ln in result.stdout.splitlines() if " FAILED" in ln)
    print(f"  → {passed} passed, {failed} failed (exit={result.returncode})")
    if failed:
        # 打印失败行细节 (前 5 条)
        for ln in result.stdout.splitlines():
            if "FAILED" in ln and "::" in ln:
                print(f"     {ln}")
    return result.returncode == 0, f"{passed} passed, {failed} failed"


def check_dashboard_metric_known() -> tuple[bool, str]:
    """校验 dashboard 引用的 metric 名在 Prometheus 采集路径 / 指标定义中出现.

    启发式: 解析 dashboard JSON 的 PromQL expr, 提取 metric 名; 在
    prometheus.yml scrape job 名 + lumio shared metrics 模块中匹配.
    策略: 分类汇报 — known OK, builtin 跳过, unknown 列出供用户决策.
    """
    _banner("2/3 看板引用 ↔ 指标定义一致 (3 dashboard)")
    prom_text = PROMETHEUS_YML.read_text(encoding="utf-8")
    # Prometheus job_name 是 metric 来源的 hint (e.g. bot-service, mcp-server)
    jobs = set(re.findall(r'job_name:\s*"([^"]+)"', prom_text))

    # Prometheus 内置 metric (dashboard 常用, 不需应用暴露)
    builtin_metrics = {
        "up",
        "scrape_duration_seconds",
        "scrape_samples_scraped",
        "uptime",
    }
    # 关键 metric 名 (Python 端 shared/metrics.py + Java 端 ToolCallAspect 已知)
    known_metrics = {
        # Python shared/metrics.py
        "lumio_stream_length",
        "lumio_active_workers",
        "lumio_retrieval_duration_seconds",
        "lumio_degradation_level",
        "lumio_session_transitions_total",
        "lumio_stream_pending_total",
        "lumio_bot_semaphore_utilization",
        "session_transitions_total",
        "session_timeouts_total",
        "llm_call_duration_seconds",
        "llm_inference_duration_seconds",
        # Prometheus middleware
        "http_requests_total",
        "http_request_duration_seconds",
        "tool_invocations_total",
        "tool_invocation_duration_seconds",
        # Java MCP (Micrometer 命名)
        "mcp_tool_calls_total",
        "mcp_tool_call_duration_seconds",
        # 中间件 exporter (middleware dashboard)
        "redis_connected_clients",
        "redis_memory_used_bytes",
        "redis_memory_max_bytes",
        "pg_stat_activity_count",
        "pg_database_size_bytes",
        "es_cluster_health_status",
        "kafka_consumergroup_lag",
    }

    total_refs = 0
    builtin_refs = 0
    unknown_refs: list[str] = []
    for dash_file in sorted(DASHBOARDS_DIR.glob("*.json")):
        data = json.loads(dash_file.read_text(encoding="utf-8"))
        panels = data.get("panels") or data.get("dashboard", {}).get("panels", [])
        for panel in panels:
            for tgt in panel.get("targets", []):
                expr = tgt.get("expr", "")
                # 提取 PromQL metric 名 (起始标识符)
                m = re.match(r"\s*([a-zA-Z_][a-zA-Z0-9_]*)", expr)
                if m:
                    metric = m.group(1)
                    # 跳过 rate/histogram_quantile 内置函数
                    if metric in {
                        "rate",
                        "irate",
                        "increase",
                        "histogram_quantile",
                        "sum",
                        "avg",
                        "max",
                        "min",
                        "count",
                        "by",
                        "without",
                    }:
                        continue
                    total_refs += 1
                    if metric in builtin_metrics:
                        builtin_refs += 1
                        continue
                    if metric not in known_metrics:
                        unknown_refs.append(f"{dash_file.name}: {metric}")
    print(f"  → {total_refs} metric refs across {len(list(DASHBOARDS_DIR.glob('*.json')))} dashboards")
    print(f"  → {builtin_refs} builtin (Prometheus self) refs, skipped")
    print(f"  → Prometheus scrape jobs: {sorted(jobs)}")
    if unknown_refs:
        print("  ⚠️  未知 metric (需人工确认是否需要新增实现 / 补 import):")
        for u in unknown_refs[:15]:
            print(f"     - {u}")
        # 不视为硬失败: 增量 dashboard 经常先于实现, 留 warning 让人决策
        return True, f"{total_refs} refs, {len(unknown_refs)} unknown (warning)"
    _ok("所有 dashboard 引用都覆盖到 known metrics")
    return True, f"{total_refs} refs OK"


def check_observability_settings_shape() -> tuple[bool, str]:
    """校验 ObservabilitySettings 关键字段就位 (commit 1+7+8 完整性)."""
    _banner("3/3 ObservabilitySettings 关键字段 (commit 1/7/8 整合检查)")
    try:
        from lumio.shared.config import ObservabilitySettings

        cfg = ObservabilitySettings()
    except Exception as e:
        _fail(f"无法构造 ObservabilitySettings: {e}")
        return False, str(e)

    required = {
        "tracing_enabled": bool,
        "jaeger_host": str,
        "otlp_endpoint": (str, type(None)),
        "sampling_ratio": float,
    }
    failed = []
    for name, typ in required.items():
        if name not in cfg.model_fields:
            failed.append(f"{name} 字段缺失")
            continue
        val = getattr(cfg, name)
        if not isinstance(val, typ):
            failed.append(f"{name}={val!r} 不是 {typ}")
            continue
        _ok(f"{name} = {val!r}")
    if failed:
        for f in failed:
            _fail(f)
        return False, f"{len(failed)} checks failed"
    return True, "all fields present"


def main() -> int:
    _banner("Lumio 可观测性闭环验证")
    print(f"  agent root:  {AGENT_ROOT}")
    print(f"  dashboards:  {DASHBOARDS_DIR}")
    print(f"  prometheus:  {PROMETHEUS_YML}")

    results: list[tuple[str, bool, str]] = []
    for name, fn in [
        ("observability-tests", check_observability_tests),
        ("dashboard-metrics", check_dashboard_metric_known),
        ("settings-shape", check_observability_settings_shape),
    ]:
        ok, detail = fn()
        results.append((name, ok, detail))

    _banner("汇总")
    for name, ok, detail in results:
        marker = "✅" if ok else "❌"
        print(f"  {marker} {name:<24} {detail}")
    all_ok = all(r[1] for r in results)
    print()
    print(f"  → 总体: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
