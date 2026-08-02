"""P1-3.2 检索失败计入 SLO 错误率 (status='failed')

覆盖:
- retrieve.py 失败路径打 RETRIEVE_COUNT{status='failed'} 计数
- 现有 success/degraded 路径不变
- Prometheus 标签值集合 {success, degraded, failed} 三态
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

httpx = pytest.importorskip("httpx")
fastapi = pytest.importorskip("fastapi")


class TestRetrieveFailureMetric:
    """P1-3.2: 检索失败 → RETRIEVE_COUNT{status='failed'}"""

    @pytest.mark.asyncio
    async def test_failed_status_incremented_on_exception(self):
        """engine.retrieve() 抛异常时, status='failed' 计数被 inc"""
        from kb.middleware.prometheus import RETRIEVE_COUNT

        # mock labels() 返回可 inc 的对象
        mock_labels = MagicMock()
        with patch.object(RETRIEVE_COUNT, "labels", return_value=mock_labels) as mock_labels_call:
            # 模拟 retrieve() 抛异常
            with patch("kb.api.retrieve.retrieve", new=AsyncMock(side_effect=RuntimeError("engine boom"))):
                from kb.api.retrieve import retrieve_documents
                from kb.retrieval.models import RetrieveRequest

                req_body = RetrieveRequest(query="test", top_k=5)
                # mock 全部 deps
                es = MagicMock()
                embedding = MagicMock()
                reranker = MagicMock()
                redis = MagicMock()
                db = MagicMock()
                principal = MagicMock(tenant_id="default", roles=["user"])
                request = MagicMock()
                request.app.state.embedding_breaker = None
                request.app.state.drift_monitor = None

                with pytest.raises(RuntimeError, match="engine boom"):
                    await retrieve_documents(
                        request_body=req_body,
                        es=es, embedding=embedding, reranker=reranker, redis=redis, db=db,
                        principal=principal, request=request,
                    )

                # 验证 RETRIEVE_COUNT.labels(status="failed").inc() 被调用
                assert mock_labels_call.called
                call_kwargs = mock_labels_call.call_args.kwargs
                assert call_kwargs.get("status") == "failed"
                assert call_kwargs.get("search_type") == "hybrid"
                mock_labels.inc.assert_called_once()

    @pytest.mark.asyncio
    async def test_success_path_unchanged(self):
        """成功路径 → status='success' (engine 内部打, 与 P1-3.2 失败路径互不影响)"""
        # engine.retrieve() 内部 line 564 已打 status="success" 或 "degraded"
        # retrieve.py 失败路径只是补 status="failed", 成功路径不重复打
        # 这里只验证 retrieve.py 失败路径不干扰成功路径
        from kb.middleware.prometheus import RETRIEVE_COUNT

        with patch.object(RETRIEVE_COUNT, "labels", return_value=MagicMock()) as mock_labels:
            from kb.api.retrieve import retrieve_documents
            from kb.retrieval.models import RetrieveRequest, RetrieveResponse

            # 模拟 retrieve() 成功返回
            success_resp = RetrieveResponse(results=[], total_candidates=0, latency_ms=10, degraded=False, degraded_stages=[])
            with patch("kb.api.retrieve.retrieve", new=AsyncMock(return_value=success_resp)):
                req_body = RetrieveRequest(query="test", top_k=5)
                es = MagicMock()
                embedding = MagicMock()
                reranker = MagicMock()
                redis = MagicMock()
                db = MagicMock()
                principal = MagicMock(tenant_id="default", roles=["user"])
                request = MagicMock()
                request.app.state.embedding_breaker = None
                request.app.state.drift_monitor = None

                # 必须不抛
                await retrieve_documents(
                    request_body=req_body,
                    es=es, embedding=embedding, reranker=reranker, redis=redis, db=db,
                    principal=principal, request=request,
                )

                # 成功路径 → retrieve.py 不应自己打 failed/success
                # (engine 内部会打, 但那是另一个 mock 范围)
                # 验证 retrieve.py 端没有任何 RETRIEVE_COUNT.labels() 调用
                # 全部调用必须来自 mock 后端 (audit.log_retrieval 等) 不带 status="failed"
                for call in mock_labels.call_args_list:
                    if call.kwargs.get("status") == "failed":
                        pytest.fail("成功路径不应有 status='failed' 打点")

    @pytest.mark.asyncio
    async def test_metric_record_failure_does_not_break_response(self):
        """即使 metric 记录本身失败, 异常仍然被正确 re-raise"""
        from kb.middleware.prometheus import RETRIEVE_COUNT

        with patch.object(RETRIEVE_COUNT, "labels", side_effect=Exception("prom broken")):
            from kb.api.retrieve import retrieve_documents
            from kb.retrieval.models import RetrieveRequest

            req_body = RetrieveRequest(query="test", top_k=5)
            with patch("kb.api.retrieve.retrieve", new=AsyncMock(side_effect=ValueError("engine err"))):
                es = MagicMock()
                embedding = MagicMock()
                reranker = MagicMock()
                redis = MagicMock()
                db = MagicMock()
                principal = MagicMock(tenant_id="default", roles=["user"])
                request = MagicMock()
                request.app.state.embedding_breaker = None
                request.app.state.drift_monitor = None

                # 原始异常必须 re-raise, metric 失败不掩盖
                with pytest.raises(ValueError, match="engine err"):
                    await retrieve_documents(
                        request_body=req_body,
                        es=es, embedding=embedding, reranker=reranker, redis=redis, db=db,
                        principal=principal, request=request,
                    )


class TestSLOAvailabilityIntegration:
    """P1-3.2: 失败计数 → PromQL availability 端到端

    验证: 失败请求会出现在 rate(kb_retrieve_total{status='failed'}) 中
    """

    def test_promql_references_failed_status(self):
        """slo.py 生成的 availability PromQL 必须引用 status='failed'"""
        from kb.observability.slo import DEFAULT_SLOS, generate_prometheus_rules

        yaml_str = generate_prometheus_rules(DEFAULT_SLOS)
        assert 'status="failed"' in yaml_str
        # 不应再用旧的 kb_retrieve_errors_total
        assert "kb_retrieve_errors_total" not in yaml_str

    def test_three_status_values_documented(self):
        """记录: P1-3.2 引入 status='failed', 现在 status 取值有 3 个"""
        # 这是文档性测试, 防止以后无意中删掉 status='failed' 分支
        from kb.api.retrieve import retrieve_documents
        from kb.retrieval.engine import retrieve as engine_retrieve

        # 验证 retrieve.py 失败路径显式 import RETRIEVE_COUNT
        import inspect

        source = inspect.getsource(retrieve_documents)
        assert 'status="failed"' in source

        # 验证 engine 内部仍处理 success/degraded (三元表达式)
        engine_source = inspect.getsource(engine_retrieve)
        assert 'status="success"' in engine_source
        assert '"degraded"' in engine_source  # 动态构造: success if not is_degraded else "degraded"
