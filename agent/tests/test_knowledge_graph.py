"""知识图谱模块单元测试

覆盖 B1 接线后依赖的 knowledge_graph 公开接口：
- query_entity_relations: 实体关系查询
- enrich_retrieval_context: RAG 上下文 KG 增强（bot_agent._handle_knowledge 调用）
"""

from __future__ import annotations

from smartcs.services.bot.knowledge_graph import (
    enrich_retrieval_context,
    query_entity_relations,
)


class TestQueryEntityRelations:
    """实体关系查询测试"""

    def test_query_by_entity_name_in_text(self) -> None:
        """查询文本包含实体名时返回其关系"""
        relations = query_entity_relations("", "信用卡年费怎么减免")
        entities = {r["entity"] for r in relations}
        assert "信用卡" in entities

    def test_query_no_match_returns_empty(self) -> None:
        """查询不含任何已知实体时返回空"""
        relations = query_entity_relations("", "今天天气怎么样")
        assert relations == []

    def test_relation_structure(self) -> None:
        """返回的关系包含 entity/relation/value 三字段"""
        relations = query_entity_relations("", "账单怎么查")
        assert relations, "应命中账单实体"
        for r in relations:
            assert set(r.keys()) == {"entity", "relation", "value"}

    def test_result_capped_at_10(self) -> None:
        """返回数量不超过 10 条"""
        relations = query_entity_relations("信用卡", "信用卡 账单 额度 分期 挂失")
        assert len(relations) <= 10

    def test_match_by_entity_arg(self) -> None:
        """entity 参数与图谱实体名互相包含时也能命中"""
        relations = query_entity_relations("账单", "还款")
        entities = {r["entity"] for r in relations}
        assert "账单" in entities


class TestEnrichRetrievalContext:
    """RAG 上下文 KG 增强测试"""

    def test_empty_chunks_returns_empty(self) -> None:
        """无检索块时返回空字符串"""
        assert enrich_retrieval_context("信用卡", []) == ""

    def test_no_entity_returns_original(self) -> None:
        """查询不含实体时原样返回检索块拼接"""
        chunks = ["块一内容", "块二内容"]
        result = enrich_retrieval_context("今天天气", chunks)
        assert result == "块一内容\n块二内容"
        assert "知识图谱" not in result

    def test_entity_query_appends_kg_section(self) -> None:
        """查询命中实体时追加知识图谱补充段"""
        chunks = ["年费减免政策内容"]
        result = enrich_retrieval_context("信用卡年费怎么减免", chunks)
        assert "知识图谱补充信息" in result
        assert "年费减免政策内容" in result
        # KG 关系应包含信用卡实体的关系
        assert "信用卡" in result

    def test_enriched_keeps_original_first(self) -> None:
        """增强后原始检索块仍排在 KG 补充之前"""
        chunks = ["原始检索内容"]
        result = enrich_retrieval_context("额度怎么提升", chunks)
        assert result.index("原始检索内容") < result.index("知识图谱补充信息")

    def test_bill_entity_enrichment(self) -> None:
        """账单查询触发账单实体关系"""
        result = enrich_retrieval_context("账单日和还款日区别", ["相关文档"])
        assert "账单" in result
