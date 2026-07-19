"""AI 执行器单元测试"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from smartcs.services.assist.ai_executor import AIExecutor


class TestAIExecutorRun:
    """AIExecutor.run() 测试"""

    @pytest.fixture
    def mock_deps(self) -> dict:
        script_service = MagicMock()
        script_service.retrieve = MagicMock(
            return_value=[
                {"script_id": "s1", "content": "标准话术内容", "score": 0.95, "tags": ["挂失"]},
                {"script_id": "s2", "content": "备选话术", "score": 0.6, "tags": []},
            ]
        )
        script_service.polish = AsyncMock(return_value="润色后的话术")

        alert_engine = MagicMock()
        alert_engine.check_compliance = MagicMock(return_value=[])

        return {
            "script_service": script_service,
            "alert_engine": alert_engine,
        }

    @pytest.mark.asyncio
    async def test_run_fast_path(self, mock_deps: dict) -> None:
        """话术 Top1 得分 > 0.9 走快速通路"""
        executor = AIExecutor(**mock_deps)
        result = await executor.run(
            session_id="s1",
            message="我卡丢了",
            intent="card_loss",
        )

        assert result["path"] == "fast"
        assert result["fast_path_hit"] is True
        assert result["degraded"] is False

    @pytest.mark.asyncio
    async def test_run_deep_path(self, mock_deps: dict) -> None:
        """话术低分走深度通路"""
        mock_deps["script_service"].retrieve.return_value = [
            {"script_id": "s1", "content": "模糊话术", "score": 0.3, "tags": []},
        ]

        executor = AIExecutor(**mock_deps)
        result = await executor.run(
            session_id="s1",
            message="查账单",
            intent="bill_query",
        )

        assert result["path"] == "deep"
        assert result["fast_path_hit"] is False

    @pytest.mark.asyncio
    async def test_run_compliance_block(self, mock_deps: dict) -> None:
        """合规 critical 告警时降级"""
        mock_deps["alert_engine"].check_compliance.return_value = [
            {"level": "critical", "message": "检测到违规承诺"},
        ]

        executor = AIExecutor(**mock_deps)
        result = await executor.run(
            session_id="s1",
            message="保证收益",
            intent="faq",
        )

        assert result["degraded"] is True
        assert result["degradation_type"] == "safe_fallback"

    @pytest.mark.asyncio
    async def test_run_exception_fallback(self, mock_deps: dict) -> None:
        """异常时返回安全兜底"""
        mock_deps["script_service"].retrieve = MagicMock(side_effect=RuntimeError("BOOM"))

        executor = AIExecutor(**mock_deps)
        result = await executor.run(
            session_id="s1",
            message="任意消息",
            intent="faq",
        )

        assert result["degraded"] is True
        assert result["degradation_type"] == "safe_fallback"

    @pytest.mark.asyncio
    async def test_run_no_script_service(self) -> None:
        """无话术服务时正常降级"""
        executor = AIExecutor()  # 全部为 None
        result = await executor.run(
            session_id="s1",
            message="测试",
            intent="faq",
        )

        assert "ui_schema" in result
        assert result["path"] == "deep"

    @pytest.mark.asyncio
    async def test_run_script_polish(self, mock_deps: dict) -> None:
        """快速通路时 LLM 润色话术"""
        executor = AIExecutor(**mock_deps, llm_client=MagicMock())
        result = await executor.run(
            session_id="s1",
            message="我卡丢了",
            intent="card_loss",
        )

        assert result["path"] == "fast"
        # 话术被润色了
        mock_deps["script_service"].polish.assert_awaited_once()


class TestAIExecutorRAG:
    """RAG 检索测试"""

    @pytest.mark.asyncio
    async def test_search_rag_no_es_client(self) -> None:
        executor = AIExecutor()
        result = await executor._search_rag("测试")
        assert result == {"knowledge": []}

    @pytest.mark.asyncio
    async def test_search_rag_exception(self) -> None:
        es_client = MagicMock()
        es_client.search = AsyncMock(side_effect=RuntimeError("ES down"))
        executor = AIExecutor(es_client=es_client)
        result = await executor._search_rag("测试")
        assert result == {"knowledge": []}


class TestAIExecutorCompliance:
    """合规检查测试"""

    @pytest.mark.asyncio
    async def test_check_compliance_no_alert_engine(self) -> None:
        executor = AIExecutor()
        result = await executor._check_compliance("test")
        assert result["passed"] is True

    def test_check_compliance_clean(self) -> None:
        alert_engine = MagicMock()
        alert_engine.check_compliance = MagicMock(return_value=[])
        executor = AIExecutor(alert_engine=alert_engine)

        # sync test
        import asyncio

        result = asyncio.run(executor._check_compliance("hello"))
        assert result["passed"] is True

    def test_check_compliance_warning(self) -> None:
        alert_engine = MagicMock()
        alert_engine.check_compliance = MagicMock(
            return_value=[
                {"level": "warning", "message": "注意"},
            ]
        )
        executor = AIExecutor(alert_engine=alert_engine)

        import asyncio

        result = asyncio.run(executor._check_compliance("careful"))
        assert result["passed"] is True  # warning 不阻断

    def test_check_compliance_critical(self) -> None:
        alert_engine = MagicMock()
        alert_engine.check_compliance = MagicMock(
            return_value=[
                {"level": "critical", "message": "违规"},
            ]
        )
        executor = AIExecutor(alert_engine=alert_engine)

        import asyncio

        result = asyncio.run(executor._check_compliance("bad"))
        assert result["passed"] is False


class TestAIExecutorScripts:
    """话术检索测试"""

    def test_retrieve_scripts_empty(self) -> None:
        script_service = MagicMock()
        script_service.retrieve = MagicMock(return_value=[])
        executor = AIExecutor(script_service=script_service)

        import asyncio

        result = asyncio.run(executor._retrieve_scripts("faq"))
        assert result["scripts"] == []
        assert result["top1_score"] == 0.0

    def test_retrieve_scripts_none_service(self) -> None:
        executor = AIExecutor()
        import asyncio

        result = asyncio.run(executor._retrieve_scripts("faq"))
        assert result["scripts"] == []
