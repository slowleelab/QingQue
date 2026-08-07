"""Bot Agent 单元测试（确定性路由）"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from lumio.services.bot.bot_agent import LumioAgent, _is_farewell, _is_greeting
from lumio.shared.models import (
    IntentLabel,
    IntentResult,
    PendingAction,
    SentimentLabel,
    SessionPhase,
    SessionState,
    SessionSubPhase,
)


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
        agent = LumioAgent(**mock_deps)
        result = await agent.run("test-session", "你好")

        assert result["response"] != ""
        assert result["response_source"] == "template"
        assert result["should_transfer"] is False

    @pytest.mark.asyncio
    async def test_run_farewell_fast_path(self, mock_deps: dict) -> None:
        """告别语走快速路径"""
        agent = LumioAgent(**mock_deps)
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

        agent = LumioAgent(**mock_deps)
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

        agent = LumioAgent(**mock_deps)
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

        agent = LumioAgent(**mock_deps)
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

        agent = LumioAgent(**mock_deps)
        result = await agent.run("test-session", "任意消息")

        # 分类失败不应崩溃，走降级回复
        assert result["response"] == "请再描述一下"
        assert result["response_source"] == "template"

    @pytest.mark.asyncio
    async def test_run_full_exception_triggers_hard_fallback(self, mock_deps: dict) -> None:
        """所有路径都失败时触发硬编码兜底"""
        mock_deps["classifier"].classify = AsyncMock(side_effect=RuntimeError("BOOM"))
        mock_deps["degradation_mgr"].generate_with_fallback = AsyncMock(side_effect=RuntimeError("DOUBLE BOOM"))

        agent = LumioAgent(**mock_deps)
        result = await agent.run("test-session", "任意消息")

        assert result["response_source"] == "fallback"
        assert "抱歉" in result["response"]


class TestProgressiveDisclosureRouting:
    """渐进式工具暴露路由（flag-gated，零回归）"""

    @pytest.fixture
    def mock_deps(self) -> dict:
        classifier = MagicMock()
        classifier.classify = AsyncMock(
            return_value=(
                IntentResult(primary_intent=IntentLabel.BILL_QUERY, primary_confidence=0.95),
                [],
                MagicMock(),
                "",
            )
        )
        degradation_mgr = MagicMock()
        degradation_mgr.generate_with_fallback = AsyncMock(return_value=MagicMock(content="RAG 知识回复", source="llm"))
        degradation_mgr._degrader = MagicMock()
        degradation_mgr._degrader.hardcoded_fallback = MagicMock(return_value="兜底")
        transfer_checker = MagicMock()
        transfer_checker.check = MagicMock(return_value=(False, "", ""))
        session_manager = MagicMock()
        session_manager.get_history = AsyncMock(return_value=[])
        session_manager.get_session = AsyncMock(return_value=None)
        return {
            "classifier": classifier,
            "degradation_mgr": degradation_mgr,
            "transfer_checker": transfer_checker,
            "session_manager": session_manager,
        }

    def _tool_executor(self) -> MagicMock:
        from lumio.services.bot.tool_executor import ToolExecutionResult

        te = MagicMock()
        te.has_tools = MagicMock(return_value=True)
        te.run_conversation = AsyncMock(return_value=ToolExecutionResult(content="您本期账单 8650 元", source="tool"))
        return te

    def _patch_flag(self, monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
        from lumio.shared.config import Settings

        settings = Settings()
        settings.mcp.progressive_disclosure_enabled = enabled
        monkeypatch.setattr("lumio.services.bot.bot_agent.get_settings", lambda: settings)

    @pytest.mark.asyncio
    async def test_flag_on_routes_to_tool_with_expected_names(
        self, mock_deps: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """开关开启 + 查询意图 → 进入工具编排，run_conversation 收到预期 tool_names"""
        self._patch_flag(monkeypatch, True)
        te = self._tool_executor()
        agent = LumioAgent(**mock_deps, tool_executor=te)

        result = await agent.run("s1", "帮我查账单")

        te.run_conversation.assert_awaited_once()
        kwargs = te.run_conversation.await_args.kwargs
        assert kwargs["tool_names"] == [
            "query_card_bill",
            "query_bill_detail",
            "query_annual_fee",
            "repay_credit_card",
        ]
        assert result["response"] == "您本期账单 8650 元"
        assert result["response_source"] == "tool"

    @pytest.mark.asyncio
    async def test_flag_off_keeps_knowledge_routing(self, mock_deps: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        """开关关闭 → 不进入工具编排，BILL_QUERY 仍走 knowledge/RAG（路由同现状）"""
        self._patch_flag(monkeypatch, False)
        te = self._tool_executor()
        agent = LumioAgent(**mock_deps, tool_executor=te)

        result = await agent.run("s1", "帮我查账单")

        te.run_conversation.assert_not_awaited()
        assert result["response"] == "RAG 知识回复"

    @pytest.mark.asyncio
    async def test_tool_failure_falls_back_to_knowledge(self, mock_deps: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        """工具编排异常 → 优雅回落知识问答"""
        self._patch_flag(monkeypatch, True)
        te = self._tool_executor()
        te.run_conversation = AsyncMock(side_effect=RuntimeError("tool down"))
        agent = LumioAgent(**mock_deps, tool_executor=te)

        result = await agent.run("s1", "帮我查账单")

        assert result["response"] == "RAG 知识回复"

    @pytest.mark.asyncio
    async def test_flag_on_but_no_tools_keeps_knowledge(self, mock_deps: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        """开关开启但无可用工具 → 不进入工具编排"""
        self._patch_flag(monkeypatch, True)
        te = self._tool_executor()
        te.has_tools = MagicMock(return_value=False)
        agent = LumioAgent(**mock_deps, tool_executor=te)

        result = await agent.run("s1", "帮我查账单")

        te.run_conversation.assert_not_awaited()
        assert result["response"] == "RAG 知识回复"


class TestBotAgentBranches:
    """Bot Agent 边界分支测试"""

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
        degradation_mgr._degrader.hardcoded_fallback = MagicMock(return_value="抱歉，服务暂时不可用")
        transfer_checker = MagicMock()
        transfer_checker.check = MagicMock(return_value=(False, "", ""))
        session_manager = MagicMock()
        session_manager.get_history = AsyncMock(return_value=[])
        session_manager.get_session = AsyncMock(return_value=None)
        session_manager.patch_state = AsyncMock(return_value={"ok": True, "new_version": 2})
        session_manager.add_turn = AsyncMock()
        return {
            "classifier": classifier,
            "degradation_mgr": degradation_mgr,
            "transfer_checker": transfer_checker,
            "session_manager": session_manager,
        }

    @pytest.mark.asyncio
    async def test_crisis_intervention(self, mock_deps: dict) -> None:
        """危机干预: 自伤表达 → 安抚话术 + 强制转人工"""
        from lumio.services.bot.prompts import CRISIS_RESPONSE

        agent = LumioAgent(**mock_deps)
        result = await agent.run("s1", "我不想活了，活着没意思")
        assert result["response_source"] == "template"
        assert result["should_transfer"] is True
        assert result["transfer_reason"].startswith("crisis_intervention")
        assert result["response"] == CRISIS_RESPONSE

    @pytest.mark.asyncio
    async def test_business_card_loss_transfers(self, mock_deps: dict) -> None:
        """挂失 → 直接转人工"""
        mock_deps["classifier"].classify = AsyncMock(
            return_value=(
                IntentResult(primary_intent=IntentLabel.CARD_LOSS, primary_confidence=0.95),
                [],
                MagicMock(),
                "",
            )
        )
        agent = LumioAgent(**mock_deps)
        result = await agent.run("s1", "我的卡丢了要挂失")
        assert result["should_transfer"] is True
        assert result["transfer_reason"] == "挂失业务"

    @pytest.mark.asyncio
    async def test_complaint_creates_ticket(self, mock_deps: dict) -> None:
        """投诉 → 创建工单"""
        mock_deps["classifier"].classify = AsyncMock(
            return_value=(
                IntentResult(primary_intent=IntentLabel.COMPLAINT, primary_confidence=0.9),
                [],
                MagicMock(),
                "",
            )
        )
        agent = LumioAgent(**mock_deps)
        result = await agent.run("s1", "我要投诉你们服务态度差", customer_id="c1")
        # 投诉走转人工或工单路径
        assert result["response"] != ""

    @pytest.mark.asyncio
    async def test_normalize_question(self) -> None:
        """问题归一化: 去标点/空白"""
        from lumio.services.bot.bot_agent import LumioAgent

        assert LumioAgent._normalize_question("  年费  怎么 减免？？ ") == "年费怎么减免"


class TestRepeatDetection:
    """重复提问检测"""

    def test_normalize_matches(self) -> None:
        from lumio.services.bot.bot_agent import LumioAgent

        assert LumioAgent._normalize_question("年费怎么减免？") == LumioAgent._normalize_question("年费怎么减免")
        assert LumioAgent._normalize_question(" 账单 查询 ") == LumioAgent._normalize_question("账单查询")


class TestToolExecutorPath:
    """工具编排路径 + 降级链 (test_bot_agent_new 补充)"""

    @pytest.fixture
    def agent(self) -> LumioAgent:
        classifier = MagicMock()
        classifier.classify = AsyncMock(
            return_value=(
                IntentResult(primary_intent=IntentLabel.BILL_QUERY, primary_confidence=0.9),
                [],
                MagicMock(),
                "",
            )
        )
        degradation_mgr = MagicMock()
        degradation_mgr.generate_with_fallback = AsyncMock(
            return_value=MagicMock(
                content="降级回复",
                source="template",
            )
        )
        degradation_mgr._llm = None  # 摘要路径 LLM 不可用
        transfer_checker = MagicMock()
        transfer_checker.check = MagicMock(return_value=(False, "", ""))
        session_manager = MagicMock()
        session_manager.get_history = AsyncMock(return_value=[])
        session_manager.get_session = AsyncMock(return_value=None)
        session_manager.patch_state = AsyncMock(return_value={"ok": True, "new_version": 2})
        session_manager.add_turn = AsyncMock()
        session_manager.read_state = AsyncMock(return_value=None)
        return LumioAgent(
            classifier=classifier,
            degradation_mgr=degradation_mgr,
            transfer_checker=transfer_checker,
            session_manager=session_manager,
        )

    @pytest.mark.asyncio
    async def test_tool_executor_success(self, agent: LumioAgent) -> None:
        """工具编排成功 → 工具来源回复 (直接调 _handle_business)"""
        executor = MagicMock()
        executor.has_tools = MagicMock(return_value=True)
        executor.run_conversation = AsyncMock(
            return_value=MagicMock(
                content="账单已查询",
                source="tool",
                pending_action=None,
                should_transfer=False,
                transfer_reason="",
            )
        )
        agent._tool_executor = executor
        intent = IntentResult(primary_intent=IntentLabel.REWARD_QUERY, primary_confidence=0.9)
        result = await agent._handle_business("s1", "查一下我的账单", intent, [], sentiment=SentimentLabel.NEUTRAL)
        assert result["response_source"] == "tool"
        assert result["response"] == "账单已查询"
        executor.run_conversation.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tool_executor_pending_action(self, agent: LumioAgent) -> None:
        """敏感操作 → 待确认话术 + 暂存 pending"""
        from lumio.shared.models import PendingAction

        executor = MagicMock()
        executor.has_tools = MagicMock(return_value=True)
        executor.run_conversation = AsyncMock(
            return_value=MagicMock(
                content="确认话术",
                source="tool_confirm",
                pending_action=PendingAction(action="挂失", tool_name="card_loss", args={"card": "1234"}),
                should_transfer=False,
                transfer_reason="",
            )
        )
        agent._tool_executor = executor
        agent._save_pending_action = AsyncMock()
        intent = IntentResult(primary_intent=IntentLabel.REWARD_QUERY, primary_confidence=0.9)
        result = await agent._handle_business("s1", "帮我挂失", intent, [], sentiment=SentimentLabel.NEUTRAL)
        assert result["response_source"] == "tool_confirm"
        agent._save_pending_action.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tool_executor_error_falls_back(self, agent: LumioAgent) -> None:
        """工具编排异常 → 回落降级链"""
        executor = MagicMock()
        executor.has_tools = MagicMock(return_value=True)
        executor.run_conversation = AsyncMock(side_effect=RuntimeError("mcp down"))
        agent._tool_executor = executor
        result = await agent.run("s1", "查账单")
        assert result["response_source"] in ("template", "llm")
        assert result["response"] != ""

    @pytest.mark.asyncio
    async def test_degraded_reply_triggers_transfer(self, agent: LumioAgent) -> None:
        """降级回复 (template/fallback) → 强制转人工"""
        agent._tool_executor = None
        result = await agent.run("s1", "查账单")
        assert result["should_transfer"] is True
        assert "degraded_" in result["transfer_reason"]


class TestSummaryLock:
    """对话摘要生成分支 (P1-10 增量摘要)"""

    @pytest.fixture
    def agent(self) -> LumioAgent:
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
        transfer_checker = MagicMock()
        session_manager = MagicMock()
        return LumioAgent(
            classifier=classifier,
            degradation_mgr=degradation_mgr,
            transfer_checker=transfer_checker,
            session_manager=session_manager,
        )

    @pytest.mark.asyncio
    async def test_summary_state_none(self, agent: LumioAgent) -> None:
        """会话不存在 → 跳过"""
        agent._session_manager.get_session = AsyncMock(return_value=None)
        await agent._ensure_summary("s1", [MagicMock(turn_id="t1", speaker="customer", content="hi")])

    @pytest.mark.asyncio
    async def test_summary_already_done(self, agent: LumioAgent) -> None:
        """最后裁剪轮次已摘要 → 跳过"""
        from lumio.shared.models import SessionState

        state = SessionState(
            session_id="s1",
            customer_id="c1",
            current_phase=SessionPhase.BOT,
            sub_phase=SessionSubPhase.BOT_ACTIVE,
            created_at=datetime.now(),
            last_active_at=datetime.now(),
            last_summarized_turn_id="t1",
        )
        agent._session_manager.get_session = AsyncMock(return_value=state)
        agent._session_manager.patch_state = AsyncMock(return_value={"ok": True})
        turns = [MagicMock(turn_id="t1", speaker="customer", content="hi")]
        await agent._ensure_summary("s1", turns)
        agent._session_manager.patch_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_summary_llm_unavailable(self, agent: LumioAgent) -> None:
        """LLM 不可用 → 跳过"""
        from lumio.shared.models import SessionState

        state = SessionState(
            session_id="s1",
            customer_id="c1",
            current_phase=SessionPhase.BOT,
            sub_phase=SessionSubPhase.BOT_ACTIVE,
            created_at=datetime.now(),
            last_active_at=datetime.now(),
        )
        agent._session_manager.get_session = AsyncMock(return_value=state)
        agent._degradation_mgr._llm = None
        turns = [MagicMock(turn_id="t1", speaker="customer", content="hi")]
        await agent._ensure_summary("s1", turns)

    @pytest.mark.asyncio
    async def test_summary_success(self, agent: LumioAgent) -> None:
        """摘要生成成功 → patch_state 增量写入"""
        from lumio.shared.models import SessionState

        state = SessionState(
            session_id="s1",
            customer_id="c1",
            current_phase=SessionPhase.BOT,
            sub_phase=SessionSubPhase.BOT_ACTIVE,
            created_at=datetime.now(),
            last_active_at=datetime.now(),
            version=3,
        )
        llm = MagicMock()
        llm.chat = AsyncMock(return_value="客户咨询了年费减免政策")
        agent._session_manager.get_session = AsyncMock(return_value=state)
        agent._degradation_mgr._llm = llm
        agent._session_manager.patch_state = AsyncMock(return_value={"ok": True, "new_version": 4})
        turns = [MagicMock(turn_id="t1", speaker="customer", content="年费怎么减免")]
        await agent._ensure_summary("s1", turns)
        agent._session_manager.patch_state.assert_awaited_once()
        patches = agent._session_manager.patch_state.await_args.kwargs["patches"]
        assert "年费减免" in patches["conversation_summary"]
        assert patches["last_summarized_turn_id"] == "t1"
        assert agent._session_manager.patch_state.await_args.kwargs["expected_version"] == 3

    @pytest.mark.asyncio
    async def test_summary_cas_fail(self, agent: LumioAgent) -> None:
        """CAS 写入失败 → 告警日志"""
        from lumio.shared.models import SessionState

        state = SessionState(
            session_id="s1",
            customer_id="c1",
            current_phase=SessionPhase.BOT,
            sub_phase=SessionSubPhase.BOT_ACTIVE,
            created_at=datetime.now(),
            last_active_at=datetime.now(),
        )
        llm = MagicMock()
        llm.chat = AsyncMock(return_value="摘要内容")
        agent._session_manager.get_session = AsyncMock(return_value=state)
        agent._degradation_mgr._llm = llm
        agent._session_manager.patch_state = AsyncMock(return_value={"ok": False})
        turns = [MagicMock(turn_id="t1", speaker="customer", content="hi")]
        await agent._ensure_summary("s1", turns)
        agent._session_manager.patch_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_summary_llm_error(self, agent: LumioAgent) -> None:
        """LLM 异常 → 跳过"""
        from lumio.shared.models import SessionState

        state = SessionState(
            session_id="s1",
            customer_id="c1",
            current_phase=SessionPhase.BOT,
            sub_phase=SessionSubPhase.BOT_ACTIVE,
            created_at=datetime.now(),
            last_active_at=datetime.now(),
        )
        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=RuntimeError("llm down"))
        agent._session_manager.get_session = AsyncMock(return_value=state)
        agent._degradation_mgr._llm = llm
        turns = [MagicMock(turn_id="t1", speaker="customer", content="hi")]
        await agent._ensure_summary("s1", turns)

    @pytest.mark.asyncio
    async def test_summary_incremental(self, agent: LumioAgent) -> None:
        """已有摘要位置 → 仅对新增轮次生成增量摘要"""
        from lumio.shared.models import SessionState

        state = SessionState(
            session_id="s1",
            customer_id="c1",
            current_phase=SessionPhase.BOT,
            sub_phase=SessionSubPhase.BOT_ACTIVE,
            created_at=datetime.now(),
            last_active_at=datetime.now(),
            conversation_summary="旧摘要",
        )
        llm = MagicMock()
        llm.chat = AsyncMock(return_value="增量摘要")
        agent._session_manager.get_session = AsyncMock(return_value=state)
        agent._degradation_mgr._llm = llm
        agent._session_manager.patch_state = AsyncMock(return_value={"ok": True})
        turns = [
            MagicMock(turn_id="t1", speaker="customer", content="第一轮"),
            MagicMock(turn_id="t2", speaker="customer", content="第二轮"),
        ]
        await agent._ensure_summary("s1", turns)
        # last_summarized_id 为空 → 全量摘要, 首轮即生成
        agent._session_manager.patch_state.assert_awaited_once()


class TestSessionMemory:
    """结构化会话记忆 (build_session_memory)"""

    @pytest.fixture
    def agent(self) -> LumioAgent:
        classifier = MagicMock()
        classifier.classify = AsyncMock(
            return_value=(
                IntentResult(primary_intent=IntentLabel.FAQ, primary_confidence=0.5),
                [],
                MagicMock(),
                "",
            )
        )
        return LumioAgent(
            classifier=classifier,
            degradation_mgr=MagicMock(),
            transfer_checker=MagicMock(),
            session_manager=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_memory_empty_state(self, agent: LumioAgent) -> None:
        """无会话状态 → 返回空字符串"""
        agent._session_manager.get_session = AsyncMock(return_value=None)
        assert await agent._build_session_memory("s1") == ""

    @pytest.mark.asyncio
    async def test_memory_with_entities(self, agent: LumioAgent) -> None:
        """含已知实体/槽位 → 拼装记忆"""
        from lumio.shared.models import SessionState

        state = SessionState(
            session_id="s1",
            customer_id="c1",
            current_phase=SessionPhase.BOT,
            sub_phase=SessionSubPhase.BOT_ACTIVE,
            created_at=datetime.now(),
            last_active_at=datetime.now(),
            conversation_summary="客户是白金卡用户",
            vip_level="gold",
            card_types=["visa"],
        )
        agent._session_manager.get_session = AsyncMock(return_value=state)
        memory = await agent._build_session_memory("s1")
        assert "白金卡用户" in memory
        assert "VIP等级=gold" in memory
        assert "卡种=visa" in memory


class TestPendingActionFlow:
    """敏感操作确认状态机 (confirm/cancel/unclear/expired)"""

    def _make_agent(self) -> LumioAgent:
        classifier = MagicMock()
        classifier.classify = AsyncMock(
            return_value=(
                IntentResult(primary_intent=IntentLabel.FAQ, primary_confidence=0.5),
                [],
                MagicMock(),
                "",
            )
        )
        session_manager = MagicMock()
        session_manager.get_session = AsyncMock(return_value=None)
        return LumioAgent(
            classifier=classifier,
            degradation_mgr=MagicMock(_degrader=MagicMock(hardcoded_fallback=MagicMock(return_value="降级话术"))),
            transfer_checker=MagicMock(),
            session_manager=session_manager,
        )

    def _state(self, pending: PendingAction) -> SessionState:
        return SessionState(
            session_id="s1",
            customer_id="c1",
            current_phase=SessionPhase.BOT,
            sub_phase=SessionSubPhase.BOT_ACTIVE,
            created_at=datetime.now(),
            last_active_at=datetime.now(),
            version=5,
            pending_action=pending,
        )

    def _pending(self, **kw) -> PendingAction:
        defaults = dict(
            tool_name="card_loss",
            arguments={"card": "1234"},
            tool_call_id="tc-1",
            confirm_prompt="请问是否办理挂失？",
        )
        defaults.update(kw)
        return PendingAction(**defaults)

    @pytest.mark.asyncio
    async def test_expired_clears_pending(self) -> None:
        """已过期 → 清除 + expired 审计"""
        from datetime import timedelta

        agent = self._make_agent()
        te = MagicMock()
        te.audit_decision = AsyncMock()
        te.execute_confirmed_action = AsyncMock()
        agent._tool_executor = te
        agent._clear_pending_action = AsyncMock()
        pending = self._pending(expires_at=datetime.now(UTC) - timedelta(seconds=5))
        state = self._state(pending)
        result = await agent._handle_pending_action("s1", "确认", state, "c1")
        assert result["response_source"] == "template"
        assert "超时失效" in result["response"]
        agent._clear_pending_action.assert_awaited_once()
        te.audit_decision.assert_awaited_once()
        assert te.audit_decision.await_args.kwargs["decision"] == "expired"

    @pytest.mark.asyncio
    async def test_confirm_executes_tool(self) -> None:
        """确认 → 执行敏感操作 + 幂等键 + 清除 pending"""
        agent = self._make_agent()
        te = MagicMock()
        te.audit_decision = AsyncMock()
        te.execute_confirmed_action = AsyncMock(return_value=MagicMock(content="挂失已受理", source="tool"))
        agent._tool_executor = te
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        agent._session_manager._redis = redis
        agent._clear_pending_action = AsyncMock()
        state = self._state(self._pending())
        result = await agent._handle_pending_action("s1", "是的，确认", state, "c1")
        assert result["response"] == "挂失已受理"
        te.execute_confirmed_action.assert_awaited_once()
        te.audit_decision.assert_awaited_once()
        assert te.audit_decision.await_args.kwargs["decision"] == "confirm"
        agent._clear_pending_action.assert_awaited_once()
        # 幂等键写入
        assert redis.setex.await_args.args[0] == "lumio:tool:executed:tc-1"

    @pytest.mark.asyncio
    async def test_confirm_idempotent_skip(self) -> None:
        """确认但已执行过 (幂等键命中) → 提示完成不重复执行"""
        agent = self._make_agent()
        te = MagicMock()
        te.audit_decision = AsyncMock()
        te.execute_confirmed_action = AsyncMock()
        agent._tool_executor = te
        redis = AsyncMock()
        redis.get = AsyncMock(return_value="1")
        agent._session_manager._redis = redis
        agent._clear_pending_action = AsyncMock()
        state = self._state(self._pending())
        result = await agent._handle_pending_action("s1", "确认", state, "c1")
        assert "无需重复办理" in result["response"]
        te.execute_confirmed_action.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirm_execution_failure(self) -> None:
        """确认但执行失败 → 清除 + 降级话术"""
        agent = self._make_agent()
        te = MagicMock()
        te.audit_decision = AsyncMock()
        te.execute_confirmed_action = AsyncMock(side_effect=RuntimeError("tool down"))
        agent._tool_executor = te
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        agent._session_manager._redis = redis
        agent._clear_pending_action = AsyncMock()
        state = self._state(self._pending())
        result = await agent._handle_pending_action("s1", "确认", state, "c1")
        assert result["response_source"] == "fallback"
        agent._clear_pending_action.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_clears_pending(self) -> None:
        """取消 → 清除 + cancel 审计"""
        agent = self._make_agent()
        te = MagicMock()
        te.audit_decision = AsyncMock()
        agent._tool_executor = te
        agent._clear_pending_action = AsyncMock()
        state = self._state(self._pending())
        result = await agent._handle_pending_action("s1", "取消", state, "c1")
        assert "已为您取消" in result["response"]
        agent._clear_pending_action.assert_awaited_once()
        assert te.audit_decision.await_args.kwargs["decision"] == "cancel"

    @pytest.mark.asyncio
    async def test_unclear_below_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """无法判定 → 计数 +1 并重复确认话术"""
        from lumio.shared.config import Settings

        settings = Settings()
        settings.mcp.unclear_auto_cancel_threshold = 3
        monkeypatch.setattr("lumio.services.bot.bot_agent.get_settings", lambda: settings)

        agent = self._make_agent()
        te = MagicMock()
        te.audit_decision = AsyncMock()
        agent._tool_executor = te
        agent._session_manager.patch_state = AsyncMock(return_value={"ok": True})
        state = self._state(self._pending())
        result = await agent._handle_pending_action("s1", "换个问题问问", state, "c1")
        assert "请问是否办理挂失" in result["response"]
        agent._session_manager.patch_state.assert_awaited_once()
        patches = agent._session_manager.patch_state.await_args.kwargs["patches"]
        assert patches["pending_action"]["unclear_count"] == 1

    @pytest.mark.asyncio
    async def test_unclear_auto_cancel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """连续无法判定 3 次 → 自动取消 + pending_released 放行新消息"""
        from lumio.shared.config import Settings

        settings = Settings()
        settings.mcp.unclear_auto_cancel_threshold = 3
        monkeypatch.setattr("lumio.services.bot.bot_agent.get_settings", lambda: settings)

        agent = self._make_agent()
        te = MagicMock()
        te.audit_decision = AsyncMock()
        agent._tool_executor = te
        agent._clear_pending_action = AsyncMock()
        pending = self._pending()
        pending.unclear_count = 2
        state = self._state(pending)
        result = await agent._handle_pending_action("s1", "继续问别的", state, "c1")
        assert result.get("pending_released") is True
        agent._clear_pending_action.assert_awaited_once()
        assert te.audit_decision.await_args.kwargs["decision"] == "unclear_auto_cancel"
