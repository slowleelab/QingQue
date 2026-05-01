"""实验 3: Reranker 对比

对比有/无 Reranker 对检索精度和延迟的影响。
需要 bot 服务运行中 (make dev)。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.experiments.metrics import hit_at_k, mrr, ndcg_at_k, precision_at_k
from scripts.experiments.runner import (
    BOT_BASE_URL,
    EVAL_DATA_PATH,
    format_results_table,
    load_eval_data,
    run_eval_batch,
    save_report,
)


def main() -> None:
    print("=" * 60)
    print("实验 3: Reranker 对比")
    print("=" * 60)

    eval_data = load_eval_data(EVAL_DATA_PATH)
    print(f"加载评估数据: {len(eval_data)} 条")

    results: list[dict] = []

    for rerank in [False, True]:
        label = "Reranker 开启" if rerank else "Reranker 关闭"
        print(f"\n运行: {label}...")

        relevances, avg_latency = run_eval_batch(
            eval_data=eval_data,
            search_type="hybrid",
            rerank=rerank,
            top_k=5,
            base_url=BOT_BASE_URL,
        )

        metrics = {
            "mrr": mrr(relevances),
            "hit@3": hit_at_k(relevances, 3),
            "hit@5": hit_at_k(relevances, 5),
            "ndcg@5": ndcg_at_k(relevances, 5),
            "p@3": precision_at_k(relevances, 3),
            "avg_latency_ms": round(avg_latency, 1),
        }
        print(f"  MRR={metrics['mrr']:.4f}, P@3={metrics['p@3']:.4f}, 延迟={metrics['avg_latency_ms']}ms")

        results.append({
            "params": {"Reranker": "开启" if rerank else "关闭"},
            "metrics": metrics,
        })

    # 输出对比表
    table = format_results_table(results)
    print(f"\n{table}")

    # 保存报告
    report_path = save_report("experiment3_reranker", results)
    print(f"\n报告已保存: {report_path}")


if __name__ == "__main__":
    main()
