"""实验运行器

提供评估数据加载、检索调用、相关性判定、结果格式化等通用功能。
实验脚本通过 httpx 调用运行中的 bot 服务 (localhost:8000)。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

# 默认配置
BOT_BASE_URL = "http://localhost:8000"
EVAL_DATA_PATH = "test_data/eval_qa_pairs.jsonl"
REPORTS_DIR = "scripts/experiments/reports"


def load_eval_data(path: str = EVAL_DATA_PATH) -> list[dict]:
    """加载评估 Q&A 数据集 (JSONL 格式)

    Args:
        path: JSONL 文件路径

    Returns:
        评估数据列表，每项包含 id, question, expected_answer, source_docs, category, difficulty, question_type
    """
    items: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def call_retrieve(
    query: str,
    top_k: int = 5,
    search_type: str = "hybrid",
    rerank: bool = True,
    rrf_k: int | None = None,
    filters: dict | None = None,
    base_url: str = BOT_BASE_URL,
) -> dict:
    """调用知识库检索 API

    Args:
        query: 查询文本
        top_k: 返回结果数
        search_type: 检索类型 (hybrid / bm25_only / vector_only)
        rerank: 是否启用 Reranker
        rrf_k: RRF k 参数覆盖
        filters: 过滤条件
        base_url: 服务基础 URL

    Returns:
        API 响应 dict (results, total_candidates, latency_ms)
    """
    payload: dict[str, Any] = {
        "query": query,
        "top_k": top_k,
        "search_type": search_type,
        "rerank": rerank,
    }
    if rrf_k is not None:
        payload["rrf_k"] = rrf_k
    if filters:
        payload["filters"] = filters

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(f"{base_url}/api/kb/retrieve", json=payload)
        resp.raise_for_status()
        return resp.json()


def compute_relevance(
    results: list[dict],
    source_docs: list[str],
    doc_id_to_filename: dict[str, str] | None = None,
) -> list[bool]:
    """判定检索结果是否与评估问题的来源文档相关

    判定逻辑：
    1. 如果有 doc_id_to_filename 映射，通过 doc_id 精确匹配
    2. 否则通过 content 关键词重叠模糊匹配

    Args:
        results: 检索结果列表 (来自 call_retrieve 的 results 字段)
        source_docs: 评估问题的来源文档文件名列表
        doc_id_to_filename: doc_id → filename 映射（可选）

    Returns:
        相关性布尔列表，与 results 等长
    """
    relevances: list[bool] = []
    for result in results:
        is_relevant = False
        metadata = result.get("metadata", {})

        if doc_id_to_filename:
            # 精确匹配：通过 doc_id 找文件名
            source_doc = result.get("source_doc", "")
            filename = doc_id_to_filename.get(source_doc, "")
            if filename:
                # 检查文件名是否在 source_docs 列表中
                for sd in source_docs:
                    if sd in filename or filename in sd:
                        is_relevant = True
                        break

        if not is_relevant:
            # 模糊匹配：检查 content 是否包含 source_docs 的关键词
            content = result.get("content", "")
            for sd in source_docs:
                # 从文件名提取关键词（去掉 .md 后缀和前缀）
                keywords = sd.replace(".md", "").replace("_", " ").split()
                if any(kw in content for kw in keywords if len(kw) > 2):
                    is_relevant = True
                    break

        relevances.append(is_relevant)
    return relevances


def build_doc_id_to_filename_map(base_url: str = BOT_BASE_URL) -> dict[str, str]:
    """从 API 获取 doc_id → filename 映射

    通过查询数据库获取所有文档的 id 和 file_path 映射。
    注意：此函数假设有一个管理 API 端点可用。
    如果不可用，返回空 dict，runner 会降级到模糊匹配。
    """
    # Sprint 2 暂无文档列表 API，返回空 dict
    # 后续 Sprint 可添加 GET /api/kb/documents 端点
    return {}


def format_results_table(results: list[dict]) -> str:
    """格式化实验结果为 Markdown 对比表

    Args:
        results: 实验结果列表，每个 dict 包含 params 和 metrics

    Returns:
        Markdown 表格字符串
    """
    if not results:
        return "无实验结果"

    # 收集所有列
    all_keys: set[str] = set()
    for r in results:
        all_keys.update(r.get("params", {}).keys())
        all_keys.update(r.get("metrics", {}).keys())

    # 固定列顺序
    param_keys = sorted(k for k in all_keys if k in results[0].get("params", {}))
    metric_keys = ["mrr", "hit@3", "hit@5", "ndcg@5", "p@3"]
    metric_keys = [k for k in metric_keys if k in all_keys]
    remaining_metric_keys = sorted(
        k for k in all_keys if k not in set(metric_keys) and k in results[0].get("metrics", {})
    )
    all_cols = param_keys + metric_keys + remaining_metric_keys
    if "avg_latency_ms" in all_keys:
        if "avg_latency_ms" not in all_cols:
            all_cols.append("avg_latency_ms")

    # 表头
    header = "| " + " | ".join(all_cols) + " |"
    separator = "| " + " | ".join(["---"] * len(all_cols)) + " |"

    # 数据行
    rows: list[str] = []
    for r in results:
        values: list[str] = []
        combined = {**r.get("params", {}), **r.get("metrics", {})}
        for col in all_cols:
            val = combined.get(col, "")
            if isinstance(val, float):
                values.append(f"{val:.4f}")
            else:
                values.append(str(val))
        rows.append("| " + " | ".join(values) + " |")

    return "\n".join([header, separator] + rows)


def save_report(experiment_name: str, results: list[dict], output_dir: str = REPORTS_DIR) -> str:
    """保存实验报告到文件

    Args:
        experiment_name: 实验名称
        results: 实验结果列表
        output_dir: 输出目录

    Returns:
        报告文件路径
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{output_dir}/{experiment_name}_{timestamp}.md"

    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"# {experiment_name}\n\n")
        f.write(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(format_results_table(results))
        f.write("\n")

    return filename


def run_eval_batch(
    eval_data: list[dict],
    search_type: str = "hybrid",
    rerank: bool = True,
    rrf_k: int | None = None,
    top_k: int = 5,
    base_url: str = BOT_BASE_URL,
) -> tuple[list[list[bool]], float]:
    """对评估数据集批量运行检索并计算相关性

    Args:
        eval_data: 评估数据列表
        search_type: 检索类型
        rerank: 是否启用 Reranker
        rrf_k: RRF k 参数
        top_k: 返回结果数
        base_url: 服务 URL

    Returns:
        (relevances, avg_latency_ms) 元组
    """
    all_relevances: list[list[bool]] = []
    total_latency = 0.0

    for item in eval_data:
        try:
            resp = call_retrieve(
                query=item["question"],
                top_k=top_k,
                search_type=search_type,
                rerank=rerank,
                rrf_k=rrf_k,
                base_url=base_url,
            )
            total_latency += resp.get("latency_ms", 0)
            rels = compute_relevance(resp.get("results", []), item.get("source_docs", []))
            all_relevances.append(rels)
        except Exception as e:
            print(f"  查询失败: {item['id']} - {e}")
            all_relevances.append([])

    avg_latency = total_latency / len(eval_data) if eval_data else 0
    return all_relevances, avg_latency
