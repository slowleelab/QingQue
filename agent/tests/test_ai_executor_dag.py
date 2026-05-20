"""AI 服务执行器 LangGraph DAG 单元测试"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from smartcs.services.assist.ai_executor_dag import AIExecutorDAG


def _make_mock_script_service(top1_score: float = 0.95):
    """创建 mock ScriptService

    Args:
        top1_score: Top1 检索得分，>0.9 走快速通路，<=0.9 走深度通路
    """
    svc = MagicMock()
    svc.retrieve = MagicMock(
        return_value=[
            {"script_id": "s1", "content": "话术内容", "tags": ["faq"], "priority": 5, "score": top1_score}
        ]
    )
    svc.polish = MagicMock(side_effect=lambda s, *a, **kw: s.get("content", "") if isinstance(s, dict) else str(s))
    return svc


def _make_mock_alert_engine(clean: bool = True):
    """创建 mock AlertEngine

    Args:
        clean: True=无违规, False=触发 CRITICAL 合规拦截
    """
    engine = MagicMock()
    if clean:
        engine.check_compliance = MagicMock(return_value=[])
    else:
        engine.check_compliance = MagicMock(
            return_value=[
                {
                    "level": "critical",
                    "category": "compliance",
                    "message": "违规",
                    "suggestion": "停止",
                }
            ]
        )
    return engine


class TestDAGStructure:
    """DAG 图结构验证"""

    def test_dag_compiles_successfully(self):
        dag = AIExecutorDAG(script_service=_make_mock_script_service())
        assert dag.graph is not None

    def test_dag_has_expected_nodes(self):
        """验证编译后的图包含所有预期节点"""
        dag = AIExecutorDAG(script_service=_make_mock_script_service())
        # LangGraph CompiledGraph stores nodes in .nodes, includes __start__/__end__ internals
        node_names = set(dag.graph.nodes.keys())
        expected = {
            "check_fast",
            "fast_retrieval",
            "monitor_hit",
            "rag_chain",
            "kg_chain",
            "normalize",
            "rerank",
            "firewall",
            "fallback",
            "format_out",
        }
        # All expected nodes must be present (LangGraph may add internal nodes like __start__)
        assert expected.issubset(node_names)


class TestFastPath:
    """快速通路: Top1 > 0.9"""

    @pytest.mark.asyncio
    async def test_high_confidence_takes_fast_path(self):
        dag = AIExecutorDAG(
            script_service=_make_mock_script_service(top1_score=0.95),
            alert_engine=_make_mock_alert_engine(clean=True),
        )
        result = await dag.run(
            session_id="s1",
            message="查询",
            intent="faq",
            confidence=0.95,
        )
        assert result["path"] == "fast"
        assert result["fast_path_hit"] is True

    @pytest.mark.asyncio
    async def test_fast_path_includes_script(self):
        dag = AIExecutorDAG(
            script_service=_make_mock_script_service(top1_score=0.95),
            alert_engine=_make_mock_alert_engine(clean=True),
        )
        result = await dag.run(
            session_id="s1",
            message="查询",
            intent="faq",
            confidence=0.95,
        )
        assert result["ui_schema"].get("scripts") is not None
        assert len(result["ui_schema"]["scripts"]) > 0

    @pytest.mark.asyncio
    async def test_fast_path_no_match(self):
        """话术检索无结果时 fast_path_hit=False，走深度通路"""
        svc = MagicMock()
        svc.retrieve = MagicMock(return_value=[])
        dag = AIExecutorDAG(
            script_service=svc,
            alert_engine=_make_mock_alert_engine(clean=True),
        )
        result = await dag.run(
            session_id="s1",
            message="查询",
            intent="chitchat",
            confidence=0.95,
        )
        assert result["path"] == "deep"
        assert result["fast_path_hit"] is False

    @pytest.mark.asyncio
    async def test_top1_score_exactly_0_9_takes_deep_path(self):
        """top1_score == 0.9 不满足 > 0.9，走深度通路"""
        dag = AIExecutorDAG(
            script_service=_make_mock_script_service(top1_score=0.9),
            alert_engine=_make_mock_alert_engine(clean=True),
        )
        result = await dag.run(
            session_id="s1",
            message="查询",
            intent="faq",
            confidence=0.9,
        )
        assert result["path"] == "deep"


class TestDeepPath:
    """深度通路: Top1 <= 0.9"""

    @pytest.mark.asyncio
    async def test_low_confidence_takes_deep_path(self):
        dag = AIExecutorDAG(
            script_service=_make_mock_script_service(top1_score=0.6),
            alert_engine=_make_mock_alert_engine(clean=True),
        )
        result = await dag.run(
            session_id="s1",
            message="查询",
            intent="bill_query",
            confidence=0.6,
        )
        assert result["path"] == "deep"

    @pytest.mark.asyncio
    async def test_deep_path_no_es_returns_empty(self):
        """深度通路在无 ES 客户端时返回空候选"""
        dag = AIExecutorDAG(
            script_service=_make_mock_script_service(top1_score=0.5),
            alert_engine=_make_mock_alert_engine(clean=True),
            es_client=None,
        )
        result = await dag.run(
            session_id="s1",
            message="查询",
            intent="faq",
            confidence=0.5,
        )
        assert result["path"] == "deep"
        assert result["rag_candidates"] == []

    @pytest.mark.asyncio
    async def test_deep_path_normalize_limits_candidates(self):
        """候选归一化: RAG Top-5 / KG Top-3"""
        dag = AIExecutorDAG(
            script_service=_make_mock_script_service(),
            alert_engine=_make_mock_alert_engine(clean=True),
        )
        # 直接测试 _normalize 方法
        state = {
            "rag_candidates": [{"content": f"rag-{i}", "score": 0.5} for i in range(8)],
            "kg_candidates": [{"content": f"kg-{i}", "score": 0.5} for i in range(5)],
        }
        result = await dag._normalize(state)
        # RAG 截取 Top-5, KG 截取 Top-3
        assert len([c for c in result["normalized_candidates"] if c["content"].startswith("rag")]) == 5
        assert len([c for c in result["normalized_candidates"] if c["content"].startswith("kg")]) == 3


class TestRerank:
    """重排序降权"""

    @pytest.mark.asyncio
    async def test_candidates_without_adoption_rate_downweighted(self):
        """无采纳率数据的候选降权 ×0.7"""
        dag = AIExecutorDAG(
            script_service=_make_mock_script_service(),
            alert_engine=_make_mock_alert_engine(clean=True),
        )
        state = {
            "normalized_candidates": [
                {"content": "A", "score": 1.0, "adoption_rate": 0.8},
                {"content": "B", "score": 1.0},  # no adoption_rate
            ],
        }
        result = await dag._rerank(state)
        a = next(c for c in result["reranked_candidates"] if c["content"] == "A")
        b = next(c for c in result["reranked_candidates"] if c["content"] == "B")
        assert a["score"] == 1.0
        assert b["score"] == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_rerank_empty_candidates(self):
        """空候选列表直接返回空"""
        dag = AIExecutorDAG(
            script_service=_make_mock_script_service(),
            alert_engine=_make_mock_alert_engine(clean=True),
        )
        result = await dag._rerank({"normalized_candidates": []})
        assert result["reranked_candidates"] == []

    @pytest.mark.asyncio
    async def test_rerank_with_reranker(self):
        """有 reranker 时使用重排序"""
        from smartcs.shared.models import RerankResult

        mock_reranker = MagicMock()
        mock_reranker.rerank = MagicMock(
            return_value=[
                RerankResult(index=1, relevance_score=0.95, text="B"),
                RerankResult(index=0, relevance_score=0.6, text="A"),
            ]
        )
        dag = AIExecutorDAG(
            script_service=_make_mock_script_service(),
            alert_engine=_make_mock_alert_engine(clean=True),
            reranker=mock_reranker,
        )
        state = {
            "message": "查询",
            "normalized_candidates": [
                {"content": "A", "score": 0.8},
                {"content": "B", "score": 0.6},
            ],
        }
        result = await dag._rerank(state)
        # B should be first after rerank (higher rerank_score)
        assert result["reranked_candidates"][0]["content"] == "B"
        assert result["reranked_candidates"][0]["rerank_score"] == 0.95


class TestFirewall:
    """合规防火墙"""

    @pytest.mark.asyncio
    async def test_firewall_passes_clean_content(self):
        dag = AIExecutorDAG(
            script_service=_make_mock_script_service(top1_score=0.95),
            alert_engine=_make_mock_alert_engine(clean=True),
        )
        result = await dag.run(
            session_id="s1",
            message="正常查询",
            intent="faq",
            confidence=0.95,
        )
        assert result["firewall_passed"] is True
        assert result["degraded"] is False

    @pytest.mark.asyncio
    async def test_firewall_blocks_noncompliant_content(self):
        dag = AIExecutorDAG(
            script_service=_make_mock_script_service(top1_score=0.95),
            alert_engine=_make_mock_alert_engine(clean=False),
        )
        result = await dag.run(
            session_id="s1",
            message="违规内容",
            intent="faq",
            confidence=0.95,
        )
        assert result["firewall_passed"] is False
        assert result["degraded"] is True
        assert result["degradation_type"] == "safe_fallback"

    @pytest.mark.asyncio
    async def test_firewall_no_alert_engine_passes(self):
        """无 alert_engine 时默认放行"""
        dag = AIExecutorDAG(
            script_service=_make_mock_script_service(top1_score=0.95),
            alert_engine=None,
        )
        result = await dag.run(
            session_id="s1",
            message="查询",
            intent="faq",
            confidence=0.95,
        )
        assert result["firewall_passed"] is True

    @pytest.mark.asyncio
    async def test_firewall_fallback_has_safe_template(self):
        """拦截后降级话术包含兜底内容"""
        dag = AIExecutorDAG(
            script_service=_make_mock_script_service(top1_score=0.95),
            alert_engine=_make_mock_alert_engine(clean=False),
        )
        result = await dag.run(
            session_id="s1",
            message="违规内容",
            intent="faq",
            confidence=0.95,
        )
        assert result["ui_schema"].get("fallback") is not None
        assert len(result["ui_schema"]["fallback"]) > 0


class TestOutputFormat:
    """输出格式"""

    @pytest.mark.asyncio
    async def test_output_has_latency(self):
        dag = AIExecutorDAG(
            script_service=_make_mock_script_service(top1_score=0.95),
            alert_engine=_make_mock_alert_engine(clean=True),
        )
        result = await dag.run(session_id="s1", message="查询", confidence=0.95)
        assert result["latency_ms"] >= 0

    @pytest.mark.asyncio
    async def test_output_has_ui_schema(self):
        dag = AIExecutorDAG(
            script_service=_make_mock_script_service(top1_score=0.95),
            alert_engine=_make_mock_alert_engine(clean=True),
        )
        result = await dag.run(session_id="s1", message="查询", confidence=0.95)
        assert "ui_schema" in result

    @pytest.mark.asyncio
    async def test_output_preserves_trace_id(self):
        dag = AIExecutorDAG(
            script_service=_make_mock_script_service(top1_score=0.95),
            alert_engine=_make_mock_alert_engine(clean=True),
        )
        result = await dag.run(
            session_id="s1", message="查询", confidence=0.95, trace_id="trace-123"
        )
        assert result["trace_id"] == "trace-123"

    @pytest.mark.asyncio
    async def test_output_deep_path_knowledge_format(self):
        """深度通路输出包含 knowledge 字段"""
        dag = AIExecutorDAG(
            script_service=_make_mock_script_service(),
            alert_engine=_make_mock_alert_engine(clean=True),
        )
        # 直接测试 _format_out 方法
        state = {
            "degraded": False,
            "path": "deep",
            "fast_script": {},
            "reranked_candidates": [
                {"content": "知识内容1", "score": 0.9, "source": "doc1"},
                {"content": "知识内容2", "score": 0.6, "source": "doc2"},
            ],
        }
        result = await dag._format_out(state)
        assert "knowledge" in result["ui_schema"]
        assert len(result["ui_schema"]["knowledge"]) == 2
        assert result["ui_schema"]["knowledge"][0]["confidence"] == "high"
        assert result["ui_schema"]["knowledge"][1]["confidence"] == "medium"


class TestEdgeCases:
    """边界场景"""

    @pytest.mark.asyncio
    async def test_zero_confidence(self):
        """confidence=0.0 且 top1_score=0 走深度通路"""
        dag = AIExecutorDAG(
            script_service=_make_mock_script_service(top1_score=0.0),
            alert_engine=_make_mock_alert_engine(clean=True),
        )
        result = await dag.run(session_id="s1", message="查询", confidence=0.0)
        assert result["path"] == "deep"

    @pytest.mark.asyncio
    async def test_invalid_intent_defaults_to_faq(self):
        """无效 intent 默认使用 FAQ"""
        svc = MagicMock()
        svc.retrieve = MagicMock(
            return_value=[{"script_id": "s1", "content": "FAQ话术", "tags": ["faq"], "priority": 5, "score": 0.95}]
        )
        dag = AIExecutorDAG(
            script_service=svc,
            alert_engine=_make_mock_alert_engine(clean=True),
        )
        result = await dag.run(
            session_id="s1",
            message="查询",
            intent="invalid_intent",
            confidence=0.95,
        )
        assert result["fast_path_hit"] is True
        # ScriptService.retrieve 应被调用，intent 被降级为 FAQ
        svc.retrieve.assert_called()

    @pytest.mark.asyncio
    async def test_rag_chain_exception_returns_empty(self):
        """RAG 链路异常时返回空候选"""
        dag = AIExecutorDAG(
            script_service=_make_mock_script_service(top1_score=0.5),
            alert_engine=_make_mock_alert_engine(clean=True),
            es_client=MagicMock(),  # non-None, but will fail on actual retrieval
        )
        result = await dag.run(
            session_id="s1",
            message="查询",
            intent="faq",
            confidence=0.5,
        )
        # Should not crash, rag_candidates may be empty due to missing ES
        assert result["path"] == "deep"
