"""知识图谱链 — 实体关系推理

在 RAG 检索结果基础上增加实体关系推理，提升回答准确度。
用于 knowledge_agent 分支，非独立服务。

模式:
  查询 → RAG 检索 → 知识图谱推理 → 整合 → LLM 生成
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# 信用卡领域实体关系图谱（简化内存版，生产可换 Neo4j）
_ENTITY_GRAPH: dict[str, list[tuple[str, str]]] = {
    "信用卡": [
        ("has_type", "普卡/金卡/白金卡/钻石卡"),
        ("has_feature", "免年费/积分/分期/取现"),
        ("related_to", "账单/额度/还款/挂失"),
    ],
    "账单": [
        ("has_method", "纸质账单/电子账单/APP查询"),
        ("has_cycle", "账单日/还款日/宽限期"),
        ("related_to", "还款/逾期/最低还款"),
    ],
    "额度": [
        ("has_type", "固定额度/临时额度/取现额度"),
        ("has_factor", "收入/征信/用卡记录"),
        ("related_to", "提额/降额/冻结"),
    ],
    "分期": [
        ("has_type", "消费分期/账单分期/现金分期"),
        ("has_factor", "手续费/期数/金额"),
        ("related_to", "账单/额度/手续费"),
    ],
    "挂失": [
        ("has_step", "电话挂失/APP挂失/柜台挂失"),
        ("has_fee", "挂失费/补卡费"),
        ("related_to", "补卡/盗刷/风控"),
    ],
}


def query_entity_relations(entity: str, query_text: str) -> list[dict]:
    """查询实体相关知识图谱关系

    Args:
        entity: 核心实体名称（如 "账单"/"额度"）
        query_text: 用户查询文本（用于匹配相关关系）

    Returns:
        关系列表 [{"entity": "...", "relation": "...", "value": "..."}]
    """
    relations: list[dict] = []

    for entity_name, entity_relations in _ENTITY_GRAPH.items():
        # 命中条件：实体名出现在查询文本中；或 entity 参数与该实体名互相包含。
        # 注意需排除空 entity：空串会使 "entity in entity_name" 恒为 True，导致匹配所有实体。
        entity_match = bool(entity) and (entity in entity_name or entity_name in entity)
        if entity_name in query_text or entity_match:
            for rel_type, rel_value in entity_relations:
                relations.append(
                    {
                        "entity": entity_name,
                        "relation": rel_type,
                        "value": rel_value,
                    }
                )

    # 限制返回数量
    return relations[:10]


def enrich_retrieval_context(query_text: str, retrieval_chunks: list[str]) -> str:
    """用知识图谱关系增强检索上下文

    将检索到的文档片段与知识图谱关系结合，形成更完整的上下文。
    """
    if not retrieval_chunks:
        return ""

    kg_relations = []
    for entity_name in _ENTITY_GRAPH:
        if entity_name in query_text:
            kg_relations.extend(query_entity_relations(entity_name, query_text))

    if not kg_relations:
        return "\n".join(retrieval_chunks)

    # 构建知识图谱补充上下文
    kg_lines = ["## 知识图谱补充信息:"]
    for r in kg_relations:
        kg_lines.append(f"- {r['entity']} {r['relation']}: {r['value']}")

    enriched = retrieval_chunks + kg_lines
    logger.debug("知识图谱增强: 原始%d块 + KG%d条", len(retrieval_chunks), len(kg_relations))

    return "\n".join(enriched)
