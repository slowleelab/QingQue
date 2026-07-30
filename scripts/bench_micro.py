"""SmartCS 微基准测试脚本

测量纯计算路径的性能（不依赖外部服务）。
用法: poetry run python ../scripts/bench_micro.py
"""

from __future__ import annotations

import asyncio
import time


def bench(name: str, fn, iterations: int = 1000, is_async: bool = False) -> None:
    """通用微基准测量"""
    # warmup
    for _ in range(min(10, iterations // 10)):
        if is_async:
            asyncio.run(fn())
        else:
            fn()

    latencies: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        if is_async:
            asyncio.run(fn())
        else:
            fn()
        latencies.append((time.perf_counter_ns() - t0) / 1000)  # μs

    latencies.sort()
    n = len(latencies)
    p50 = latencies[n // 2]
    p95 = latencies[int(n * 0.95)]
    p99 = latencies[int(n * 0.99)]
    print(f"{name:30s} n={n:5d} p50={p50:8.1f}μs p95={p95:8.1f}μs p99={p99:8.1f}μs max={latencies[-1]:8.1f}μs")


def main() -> None:
    from smartcs.services.common.assist_engine import (
        evaluate_d1_service,
        evaluate_d2_marketing,
        evaluate_d3_risk,
    )
    from smartcs.services.common.decision import detect_scene

    state = {
        "last_confidence": 0.85,
        "d1_cooldown_remaining": 0,
        "suppress_flag": False,
        "d2_cooldown_remaining": 0,
        "emotion_vector": {"label": "positive", "score": 0.7},
    }

    print("=" * 80)
    print("SmartCS 微基准测试 (Apple M4, Python 3.11)")
    print("=" * 80)

    # 评估器
    bench("D1 服务评估", lambda: evaluate_d1_service(state))
    bench("D2 营销评估", lambda: evaluate_d2_marketing(state))
    bench("D3 风控评估", lambda: evaluate_d3_risk(state))

    # 场景检测
    messages = ["我卡丢了", "我想办卡", "查一下账单", "今天天气不错", "我不想分期"]
    for msg in messages:
        bench(f"场景检测({msg})", lambda m=msg: detect_scene(m), iterations=200)

    # 意图分类（async）
    from smartcs.services.common.classifier import IntentClassifier

    classifier = IntentClassifier()
    queries = ["信用卡年费怎么减免", "这个月账单什么时候出", "我的额度能提升吗"]

    async def classify_one(q: str = queries[0]) -> None:
        await classifier.classify(q)

    bench("意图分类(规则快路)", classify_one, is_async=True)

    print("=" * 80)
    print("说明: 以上为纯计算路径延迟，不含网络/DB/LLM 调用。")
    print("端到端延迟请用 locust 负载测试: make bench")
    print("=" * 80)


if __name__ == "__main__":
    main()
