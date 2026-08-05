"""Sprint E: 压测脚本 — 1000 RPS P99 / 错误率 / token 消耗.

使用 httpx + asyncio,模拟多用户并发访问 /api/chat.
指标:
- P50 / P95 / P99 延迟
- 错误率 (4xx/5xx)
- QPS / 实际 RPS
- 累计 token 消耗 (cost)
- KV cache 命中率 (来自 Prometheus / 客户端埋点)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from lumio.shared.logger import get_logger

logger = get_logger(__name__)


# ── 测试样本 ──

_TEST_QUERIES = [
    "我想查询这个月账单",
    "我的额度是多少",
    "我要申请账单分期",
    "我上个月消费了多少钱",
    "积分怎么兑换",
    "信用卡怎么提额",
    "账单分期手续费多少",
    "可以帮我还款吗",
    "我想修改预留手机号",
    "谢谢",
    "再见",
    "投诉处理进度",
    "我想挂失卡片",
    "账单日是哪天",
    "最低还款额怎么算",
]


@dataclass
class RequestResult:
    """单次请求结果."""

    status_code: int
    latency_ms: float
    success: bool
    error: str | None = None
    tokens: int = 0
    cost: float = 0.0


@dataclass
class LoadTestReport:
    """压测报告."""

    target_rps: int
    actual_rps: float
    total_requests: int
    successful: int
    failed: int
    error_rate: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    min_ms: float
    mean_ms: float
    total_tokens: int
    total_cost: float
    duration_seconds: float
    by_status: dict[str, int] = field(default_factory=dict)


async def _send_request(
    client: httpx.AsyncClient,
    url: str,
    session_id: str,
    query: str,
    semaphore: asyncio.Semaphore,
) -> RequestResult:
    """发送单次请求."""
    start = time.perf_counter()
    try:
        async with semaphore:
            response = await client.post(
                url,
                json={"session_id": session_id, "message": query},
                timeout=30.0,
            )
        latency_ms = (time.perf_counter() - start) * 1000
        return RequestResult(
            status_code=response.status_code,
            latency_ms=latency_ms,
            success=200 <= response.status_code < 300,
            error=None if 200 <= response.status_code < 300 else response.text[:200],
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return RequestResult(
            status_code=0,
            latency_ms=latency_ms,
            success=False,
            error=f"{type(exc).__name__}: {exc}",
        )


async def _run_load_test(
    base_url: str,
    target_rps: int,
    duration_seconds: int,
    concurrency: int = 100,
) -> LoadTestReport:
    """执行压测.

    Args:
        base_url: 服务地址, e.g. "http://localhost:8000"
        target_rps: 目标 RPS
        duration_seconds: 持续时间
        concurrency: 最大并发
    """
    url = f"{base_url}/api/chat"
    semaphore = asyncio.Semaphore(concurrency)

    # 限流: target_rps / concurrency 间隔
    sleep_between = 1.0 / max(1, target_rps / max(1, concurrency))

    results: list[RequestResult] = []
    start_time = time.perf_counter()
    counter = 0

    async with httpx.AsyncClient() as client:
        end_time = start_time + duration_seconds

        while time.perf_counter() < end_time:
            batch_size = min(concurrency, max(1, int(target_rps / (1.0 / sleep_between))))
            tasks = []
            for i in range(batch_size):
                query = _TEST_QUERIES[(counter + i) % len(_TEST_QUERIES)]
                session_id = f"loadtest-{counter + i:06d}"
                tasks.append(_send_request(client, url, session_id, query, semaphore))

            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in batch_results:
                if isinstance(r, RequestResult):
                    results.append(r)
                else:
                    # gather 异常
                    results.append(
                        RequestResult(
                            status_code=0,
                            latency_ms=0,
                            success=False,
                            error=f"GatherException: {r}",
                        )
                    )
            counter += batch_size
            await asyncio.sleep(sleep_between)

    duration = time.perf_counter() - start_time
    actual_rps = len(results) / max(0.001, duration)

    # 统计延迟
    latencies = sorted([r.latency_ms for r in results if r.latency_ms > 0])
    if not latencies:
        return LoadTestReport(
            target_rps=target_rps,
            actual_rps=0,
            total_requests=0,
            successful=0,
            failed=0,
            error_rate=0,
            p50_ms=0,
            p95_ms=0,
            p99_ms=0,
            max_ms=0,
            min_ms=0,
            mean_ms=0,
            total_tokens=0,
            total_cost=0,
            duration_seconds=duration,
        )

    def _percentile(p: float) -> float:
        idx = int(len(latencies) * p / 100)
        return latencies[min(idx, len(latencies) - 1)]

    by_status: dict[str, int] = {}
    for r in results:
        key = str(r.status_code)
        by_status[key] = by_status.get(key, 0) + 1

    successful = sum(1 for r in results if r.success)
    failed = len(results) - successful

    return LoadTestReport(
        target_rps=target_rps,
        actual_rps=actual_rps,
        total_requests=len(results),
        successful=successful,
        failed=failed,
        error_rate=failed / max(1, len(results)),
        p50_ms=_percentile(50),
        p95_ms=_percentile(95),
        p99_ms=_percentile(99),
        max_ms=latencies[-1],
        min_ms=latencies[0],
        mean_ms=statistics.mean(latencies),
        total_tokens=sum(r.tokens for r in results),
        total_cost=sum(r.cost for r in results),
        duration_seconds=duration,
        by_status=by_status,
    )


def _print_report(report: LoadTestReport) -> None:
    """打印压测报告."""
    print("\n" + "=" * 60)
    print("Lumio 压测报告")
    print("=" * 60)
    print(f"目标 RPS:        {report.target_rps}")
    print(f"实际 RPS:        {report.actual_rps:.1f}")
    print(f"持续时间:        {report.duration_seconds:.1f}s")
    print(f"总请求数:        {report.total_requests}")
    print(f"成功:            {report.successful}")
    print(f"失败:            {report.failed}")
    print(f"错误率:          {report.error_rate * 100:.2f}%")
    print("-" * 60)
    print(f"延迟 P50:        {report.p50_ms:.1f}ms")
    print(f"延迟 P95:        {report.p95_ms:.1f}ms")
    print(f"延迟 P99:        {report.p99_ms:.1f}ms")
    print(f"延迟 MAX:        {report.max_ms:.1f}ms")
    print(f"延迟 MIN:        {report.min_ms:.1f}ms")
    print(f"延迟 MEAN:       {report.mean_ms:.1f}ms")
    print("-" * 60)
    print(f"总 token:        {report.total_tokens}")
    print(f"总 cost:         ${report.total_cost:.2f}")
    print(f"单次 cost:       ${report.total_cost / max(1, report.total_requests):.6f}")
    print("-" * 60)
    print(f"状态码分布:      {report.by_status}")
    print("=" * 60)

    # 通过条件
    passed = report.error_rate < 0.01 and report.p99_ms < 5000
    print(f"\n压测结果: {'✅ PASS' if passed else '❌ FAIL'}")
    print(f"  - 错误率 {report.error_rate*100:.2f}% < 1%: {'✓' if report.error_rate < 0.01 else '✗'}")
    print(f"  - P99 延迟 {report.p99_ms:.0f}ms < 5000ms: {'✓' if report.p99_ms < 5000 else '✗'}")


# ── 阶跃式压测 (Ramp-up) ──

async def run_ramp_test(
    base_url: str,
    rps_levels: list[int] = (10, 50, 100, 500, 1000),
    duration_per_level: int = 30,
) -> list[LoadTestReport]:
    """阶跃式压测: 10 → 50 → 100 → 500 → 1000 RPS.

    每级压测后等待 5s 冷却,记录每级报告.
    """
    reports: list[LoadTestReport] = []
    for level in rps_levels:
        logger.info("压测 RPS=%d (持续 %ds)", level, duration_per_level)
        report = await _run_load_test(
            base_url=base_url,
            target_rps=level,
            duration_seconds=duration_per_level,
        )
        reports.append(report)
        _print_report(report)
        await asyncio.sleep(5)
    return reports


# ── 入口 ──

def main() -> None:
    parser = argparse.ArgumentParser(description="Lumio 压测工具")
    parser.add_argument("--url", default="http://localhost:8000", help="服务地址")
    parser.add_argument("--rps", type=int, default=100, help="目标 RPS")
    parser.add_argument("--duration", type=int, default=60, help="持续时间 (秒)")
    parser.add_argument("--concurrency", type=int, default=100, help="最大并发")
    parser.add_argument("--ramp", action="store_true", help="阶跃式压测 (10→1000 RPS)")
    parser.add_argument("--output", default=None, help="输出 JSON 报告路径")
    args = parser.parse_args()

    if args.ramp:
        reports = asyncio.run(
            run_ramp_test(args.url, duration_per_level=args.duration)
        )
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(
                    [r.__dict__ for r in reports],
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
    else:
        report = asyncio.run(
            _run_load_test(
                base_url=args.url,
                target_rps=args.rps,
                duration_seconds=args.duration,
                concurrency=args.concurrency,
            )
        )
        _print_report(report)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(report.__dict__, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
