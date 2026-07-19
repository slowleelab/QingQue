"""Bot Agent 单元测试（确定性路由）"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from smartcs.services.bot.bot_agent import SmartCSAgent, _is_farewell, _is_greeting
from smartcs.shared.models import IntentLabel, IntentResult


class TestGreetingDetection:
    def test_is_greeting_ni_hao(self) -> None:
        assert _is_greeting("你好") is True

    def test_is_greeting_hi(self) -> None:
        assert _is_greeting("hi") is True

    def test_is_greeting_hello(self) -> None:
        assert _is_greeting("hello") is True

    def test_is_greeting_zai_ma(self) -> None:
        assert _is_greeting("在吗") is True

    def test_is_greeting_no(self) -> None:
        assert _is_greeting("我想查账单") is False

    def test_is_farewell_bye(self) -> None:
        assert _is_farewell("再见") is True

    def test_is_farewell_thanks(self) -> None:
        assert _is_farewell("谢谢") is True

    def test_is_farewell_no(self) -> None:
        assert _is_farewell("还有问题") is False


class TestBotAgent:
    """Bot Agent 业务逻辑测试"""

    @pytest.fixture
    def mock_deps(self) -> dict:
        classifier = MagicMock()
        classifier.classify = AsyncMock(
            return_value=(
                IntentResult(primary_intent=IntentLabel.FAQ, primary_confidence=0.5),
                [],
                MagicMock(),
                "",
            )
        )

        degradation_mgr = MagicMock()
        degradation_mgr.generate_with_fallback = AsyncMock()
        degradation_mgr._degrader = MagicMock()
        degradation_mgr._degrader.hardcoded_fallback = MagicMock(
            return_value="抱歉，服务暂时不可用，请稍后再试或拨打客服热线。"
        )

        transfer_checker = MagicMock()
        transfer_checker.check = MagicMock(return_value=(False, "", ""))

        session_manager = MagicMock()
        session_manager.get_history = AsyncMock(return_value=[])

        return {
            "classifier": classifier,
            "degradation_mgr": degradation_mgr,
            "transfer_checker": transfer_checker,
            "session_manager": session_manager,
        }

    @pytest.mark.asyncio
    async def test_run_greeting_fast_path(self, mock_deps: dict) -> None:
        """问候语走快速路径，不调 LLM"""
        agent = SmartCSAgent(**mock_deps)
        result = await agent.run("test-session", "你好")

        assert result["response"] != ""
        assert result["response_source"] == "template"
        assert result["should_transfer"] is False

    @pytest.mark.asyncio
    async def test_run_farewell_fast_path(self, mock_deps: dict) -> None:
        """告别语走快速路径"""
        agent = SmartCSAgent(**mock_deps)
        result = await agent.run("test-session", "再见")

        assert result["response"] != ""
        assert result["response_source"] == "template"

    @pytest.mark.asyncio
    async def test_run_fallback_on_normal_message(self, mock_deps: dict) -> None:
        """正常消息走分类+降级管理器"""
        mock_deps["degradation_mgr"].generate_with_fallback.return_value = MagicMock(
            content="这是自动回复",
            source="llm",
        )

        agent = SmartCSAgent(**mock_deps)
        result = await agent.run("test-session", "帮我查一下账单")

        assert result["response"] == "这是自动回复"
        assert result["response_source"] == "llm"

    @pytest.mark.asyncio
    async def test_run_business_transfer(self, mock_deps: dict) -> None:
        """挂失意图直接转人工"""
        mock_deps["classifier"].classify = AsyncMock(
            return_value=(
                IntentResult(primary_intent=IntentLabel.CARD_LOSS, primary_confidence=0.95),
                [],
                MagicMock(),
                "",
            )
        )

        agent = SmartCSAgent(**mock_deps)
        result = await agent.run("test-session", "我卡丢了")

        assert result["should_transfer"] is True
        assert result["transfer_reason"] == "挂失业务"

    @pytest.mark.asyncio
    async def test_run_returns_compatible_dict(self, mock_deps: dict) -> None:
        """返回 dict 包含所有兼容字段"""
        mock_deps["degradation_mgr"].generate_with_fallback.return_value = MagicMock(
            content="回复内容",
            source="llm",
        )

        agent = SmartCSAgent(**mock_deps)
        result = await agent.run("test-session", "测试消息")

        assert "session_id" in result
        assert "user_input" in result
        assert "intent" in result
        assert "response" in result
        assert "response_source" in result
        assert "should_transfer" in result
        assert "transfer_reason" in result
        assert "entities" in result
        assert "sentiment" in result
        assert "domain" in result
        assert "retrieval_context" in result
        assert result["session_id"] == "test-session"

    @pytest.mark.asyncio
    async def test_run_classify_failure_graceful(self, mock_deps: dict) -> None:
        """分类失败时降级为 FAQ 并正常回复（不崩溃）"""
        mock_deps["classifier"].classify = AsyncMock(side_effect=RuntimeError("BOOM"))
        mock_deps["degradation_mgr"].generate_with_fallback.return_value = MagicMock(
            content="请再描述一下",
            source="template",
        )

        agent = SmartCSAgent(**mock_deps)
        result = await agent.run("test-session", "任意消息")

        # 分类失败不应崩溃，走降级回复
        assert result["response"] == "请再描述一下"
        assert result["response_source"] == "template"

    @pytest.mark.asyncio
    async def test_run_full_exception_triggers_hard_fallback(self, mock_deps: dict) -> None:
        """所有路径都失败时触发硬编码兜底"""
        mock_deps["classifier"].classify = AsyncMock(side_effect=RuntimeError("BOOM"))
        mock_deps["degradation_mgr"].generate_with_fallback = AsyncMock(side_effect=RuntimeError("DOUBLE BOOM"))

        agent = SmartCSAgent(**mock_deps)
        result = await agent.run("test-session", "任意消息")

        assert result["response_source"] == "fallback"
        assert "抱歉" in result["response"]
