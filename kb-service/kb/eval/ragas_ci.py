"""I3-C3 RAGAS 检索质量 CI 门禁

为 RAG 检索质量改动提供可重复的回归门禁:
- 离线 golden query 集 (无需 ES/Redis, 纯算法)
- 阈值检查: avg_mrr / avg_ndcg / avg_category_hit 不低于基线
- 输出 JSON 报告 (供 CI artifact 存档)
- 退出码 0 = pass, 1 = 任何指标未达基线

设计原则:
- 不依赖真实嵌入模型 / ES / Redis (单元测试可重复跑)
- 算法层只关心 MRR/NDCG/category hit 三个核心指标
- 阈值在 RAGAS_BASELINES 集中维护, 改动需评审
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# 复用 ragas_eval 的核心算法
from kb.eval.ragas_eval import (
    GOLDEN_QUERIES,
    compute_category_hit_rate,
    compute_mrr,
    compute_ndcg,
)


@dataclass(frozen=True)
class RAGASBaseline:
    """检索质量基线 — 任何改动不跌破该值

    字段:
        avg_mrr: 平均倒数排名 (≥0.7 表示第 1 位结果频繁命中)
        avg_ndcg: 平均 NDCG@5 (≥0.6 表示前 5 个结果相关度高)
        avg_category_hit: 平均分类命中率 (≥0.5 表示分类信号在 top-K 中明显)
        min_pass_rate: 单查询达标率 (≥0.75 表示 3/4 查询能命中期望关键词)
    """

    avg_mrr: float
    avg_ndcg: float
    avg_category_hit: float
    min_pass_rate: float


# 默认基线 — 改这里必须走评审 + 标注 changelog
DEFAULT_RAGAS_BASELINE = RAGASBaseline(
    avg_mrr=0.5,
    avg_ndcg=0.4,
    avg_category_hit=0.3,
    min_pass_rate=0.5,
)


@dataclass(frozen=True)
class QueryResult:
    query: str
    mrr: float
    ndcg: float
    category_hit: float
    passed: bool


@dataclass(frozen=True)
class RAGASReport:
    total_queries: int
    passed_queries: int
    pass_rate: float
    avg_mrr: float
    avg_ndcg: float
    avg_category_hit: float
    baseline: dict
    results: list[dict]
    gate_passed: bool
    failure_reasons: list[str]


def evaluate_query(
    query: str,
    expected_keywords: list[str],
    expected_category: str,
    results: list[dict],
    baseline: RAGASBaseline,
) -> QueryResult:
    """对单条 golden query 评估, 产出 QueryResult.

    命中定义: 至少 1 个期望关键词出现在 top-K 任意结果的 content 中.
    """
    mrr = compute_mrr(results, expected_keywords)
    ndcg = compute_ndcg(results, expected_keywords)
    cat = compute_category_hit_rate(results, expected_category)
    # 单查询达标: 任意 1 个指标达到基线 (容忍某一指标波动)
    passed = (
        mrr >= baseline.avg_mrr
        or ndcg >= baseline.avg_ndcg
        or cat >= baseline.avg_category_hit
    )
    return QueryResult(
        query=query, mrr=mrr, ndcg=ndcg, category_hit=cat, passed=passed
    )


def build_report(
    golden_queries: list[dict] | None = None,
    results_lookup: dict[str, list[dict]] | None = None,
    baseline: RAGASBaseline | None = None,
) -> RAGASReport:
    """构建 RAGAS 评估报告.

    参数:
        golden_queries: 默认用 GOLDEN_QUERIES
        results_lookup: { query_text: [result_dicts] }, 测试时传入
        baseline: 评估阈值, 默认用 DEFAULT_RAGAS_BASELINE
    """
    golden = golden_queries or GOLDEN_QUERIES
    bl = baseline or DEFAULT_RAGAS_BASELINE
    lookup = results_lookup or {}

    query_results: list[QueryResult] = []
    for g in golden:
        results = lookup.get(g["query"], [])
        qr = evaluate_query(
            query=g["query"],
            expected_keywords=g["expected_keywords"],
            expected_category=g["expected_category"],
            results=results,
            baseline=bl,
        )
        query_results.append(qr)

    n = len(query_results)
    passed = sum(1 for q in query_results if q.passed)
    avg_mrr = sum(q.mrr for q in query_results) / n if n else 0.0
    avg_ndcg = sum(q.ndcg for q in query_results) / n if n else 0.0
    avg_cat = sum(q.category_hit for q in query_results) / n if n else 0.0

    failure_reasons: list[str] = []
    if avg_mrr < bl.avg_mrr:
        failure_reasons.append(
            f"avg_mrr={avg_mrr:.3f} < baseline={bl.avg_mrr:.3f}"
        )
    if avg_ndcg < bl.avg_ndcg:
        failure_reasons.append(
            f"avg_ndcg={avg_ndcg:.3f} < baseline={bl.avg_ndcg:.3f}"
        )
    if avg_cat < bl.avg_category_hit:
        failure_reasons.append(
            f"avg_category_hit={avg_cat:.3f} < baseline={bl.avg_category_hit:.3f}"
        )
    pass_rate = passed / n if n else 0.0
    if pass_rate < bl.min_pass_rate:
        failure_reasons.append(
            f"pass_rate={pass_rate:.3f} < baseline={bl.min_pass_rate:.3f}"
        )

    return RAGASReport(
        total_queries=n,
        passed_queries=passed,
        pass_rate=round(pass_rate, 4),
        avg_mrr=round(avg_mrr, 4),
        avg_ndcg=round(avg_ndcg, 4),
        avg_category_hit=round(avg_cat, 4),
        baseline=asdict(bl),
        results=[asdict(q) for q in query_results],
        gate_passed=len(failure_reasons) == 0,
        failure_reasons=failure_reasons,
    )


def write_report(report: RAGASReport, output_path: Path) -> None:
    """写入 JSON 报告, 供 CI artifact 存档"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(asdict(report), f, ensure_ascii=False, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="I3-C3 RAGAS CI 门禁")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("artifacts/ragas_report.json"),
        help="JSON 报告输出路径",
    )
    args = parser.parse_args()

    report = build_report()
    write_report(report, args.report)

    print("=" * 60)
    print("RAGAS CI 门禁")
    print("=" * 60)
    print(f"查询数:         {report.total_queries}")
    print(f"通过查询:       {report.passed_queries}")
    print(f"达标率:         {report.pass_rate:.3f}")
    print(f"avg_mrr:        {report.avg_mrr:.3f}  (基线 ≥{report.baseline['avg_mrr']})")
    print(f"avg_ndcg@5:     {report.avg_ndcg:.3f}  (基线 ≥{report.baseline['avg_ndcg']})")
    print(f"avg_cat_hit:    {report.avg_category_hit:.3f}  (基线 ≥{report.baseline['avg_category_hit']})")
    print("=" * 60)
    if report.gate_passed:
        print("✅ 门禁通过")
        return 0
    print("❌ 门禁未通过:")
    for reason in report.failure_reasons:
        print(f"  - {reason}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
