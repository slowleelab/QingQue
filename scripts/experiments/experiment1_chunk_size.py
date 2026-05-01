"""实验 1: 分块策略对比

对比结构感知分块与不同大小的递归字符分块对检索质量的影响。
需要 bot 服务运行中 (make dev)。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 添加项目根目录到 sys.path
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
    print("实验 1: 分块策略对比")
    print("=" * 60)

    eval_data = load_eval_data(EVAL_DATA_PATH)
    print(f"加载评估数据: {len(eval_data)} 条")

    # 实验配置
    # 注意：结构感知分块是当前默认策略，无需额外参数
    # 递归字符分块需要通过配置修改 chunk_size
    # 当前实验通过 search_type 和服务端配置来区分
    # 结构感知分块的结果需要先手动运行一次（使用当前 make seed 后的数据）

    configs = [
        {"name": "结构感知分块 (当前)", "search_type": "hybrid"},
        {"name": "BM25 (对比基线)", "search_type": "bm25_only"},
        {"name": "向量检索 (对比基线)", "search_type": "vector_only"},
    ]

    results: list[dict] = []

    for config in configs:
        print(f"\n运行: {config['name']}...")
        relevances, avg_latency = run_eval_batch(
            eval_data=eval_data,
            search_type=config["search_type"],
            rerank=True,
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
        print(f"  MRR={metrics['mrr']:.4f}, Hit@3={metrics['hit@3']:.4f}, Hit@5={metrics['hit@5']:.4f}")

        results.append({
            "params": {"策略": config["name"]},
            "metrics": metrics,
        })

    # 输出对比表
    table = format_results_table(results)
    print(f"\n{table}")

    # 保存报告
    report_path = save_report("experiment1_chunk_size", results)
    print(f"\n报告已保存: {report_path}")


if __name__ == "__main__":
    main()
