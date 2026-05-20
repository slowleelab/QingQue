"""话后小结与坐席保持/恢复测试"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from smartcs.services.assist.summary import (
    _format_conversation,
    _template_summary,
    generate_call_summary,
)
from smartcs.shared.models import DialogueTurn, SentimentLabel, SessionPhase, SessionSubPhase


def _make_turn(session_id: str, speaker: str, content: str) -> DialogueTurn:
    return DialogueTurn(
        turn_id="t1",
        session_id=session_id,
        speaker=speaker,
        content=content,
        timestamp=datetime.now(),
    )


def _mock_session_manager(turns: list[DialogueTurn] | None = None) -> AsyncMock:
    mgr = AsyncMock()
    mgr.get_history = AsyncMock(return_value=turns or [])
    return mgr


# ── 小结生成 ──


@pytest.mark.asyncio
async def test_generate_summary_with_llm() -> None:
    """LLM 可用时应返回结构化小结"""
    turns = [
        _make_turn("s1", "customer", "我的信用卡年费怎么扣的"),
        _make_turn("s1", "agent", "您好，您这张卡的年费是200元"),
    ]
    mgr = _mock_session_manager(turns)
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=json.dumps({
        "customer_demand": "咨询信用卡年费扣费情况",
        "problem_category": "账单查询",
        "solution_provided": "告知年费金额和扣费规则",
        "resolution_status": "已解决",
        "sentiment": "中性",
        "key_info": {"card_last4": "1234"},
    }))

    summary = await generate_call_summary("s1", mgr, llm)
    assert summary.customer_demand == "咨询信用卡年费扣费情况"
    assert summary.problem_category == "账单查询"
    assert summary.sentiment == SentimentLabel.NEUTRAL
    assert summary.key_info.get("card_last4") == "1234"


@pytest.mark.asyncio
async def test_generate_summary_without_llm() -> None:
    """LLM 不可用时应返回模板小结"""
    turns = [
        _make_turn("s1", "customer", "我想查账单"),
        _make_turn("s1", "agent", "好的，帮您查询"),
    ]
    mgr = _mock_session_manager(turns)

    summary = await generate_call_summary("s1", mgr, llm_client=None)
    assert summary.session_id == "s1"
    assert summary.customer_demand  # 非空
    assert summary.problem_category == "其他"


@pytest.mark.asyncio
async def test_generate_summary_empty_history() -> None:
    """无对话历史时应返回空小结"""
    mgr = _mock_session_manager([])

    summary = await generate_call_summary("s1", mgr)
    assert summary.session_id == "s1"
    assert summary.customer_demand == ""


@pytest.mark.asyncio
async def test_generate_summary_llm_failure_fallback() -> None:
    """LLM 调用失败时应降级到模板小结"""
    turns = [
        _make_turn("s1", "customer", "额度怎么查"),
    ]
    mgr = _mock_session_manager(turns)
    llm = AsyncMock()
    llm.chat = AsyncMock(side_effect=RuntimeError("LLM timeout"))

    summary = await generate_call_summary("s1", mgr, llm)
    assert summary.session_id == "s1"
    assert summary.customer_demand  # 降级模板仍能提取


def test_format_conversation() -> None:
    """对话格式化测试"""
    turns = [
        _make_turn("s1", "customer", "你好"),
        _make_turn("s1", "agent", "您好"),
    ]
    result = _format_conversation(turns)
    assert "[客户] 你好" in result
    assert "[坐席] 您好" in result


# ── Hold/Resume 状态转换测试 ──


@pytest.mark.asyncio
async def test_hold_transition() -> None:
    """AG_ACTIVE → AG_ON_HOLD 状态转换"""
    from smartcs.services.common.session import SessionManager
    from unittest.mock import AsyncMock

    redis = AsyncMock()
    redis.set = AsyncMock()
    redis.lrange = AsyncMock(return_value=[])
    manager = SessionManager(redis)

    meta = json.dumps({
        "session_id": "sess-hold",
        "customer_id": None,
        "channel_type": "web",
        "current_phase": "agent",
        "sub_phase": "agent:active",
        "end_reason": None,
        "vip_level": "普通",
        "card_types": [],
        "risk_tolerance": "R2",
        "turn_count": 0,
        "last_intent": None,
        "last_entities": [],
        "confidence_history": [],
        "low_confidence_streak": 0,
        "human_request_score": 0,
        "agent_id": None,
        "transfer_reason": None,
        "transfer_summary": None,
        "created_at": datetime.now().isoformat(),
        "last_active_at": datetime.now().isoformat(),
        "version": 1,
    }, ensure_ascii=False)
    redis.get = AsyncMock(return_value=meta)

    result = await manager.transition_phase(
        "sess-hold",
        SessionPhase.AGENT,
        new_sub_phase=SessionSubPhase.AG_ON_HOLD,
        reason="agent_hold",
    )
    assert result.sub_phase == SessionSubPhase.AG_ON_HOLD


@pytest.mark.asyncio
async def test_resume_transition() -> None:
    """AG_ON_HOLD → AG_ACTIVE 状态转换"""
    from smartcs.services.common.session import SessionManager
    from unittest.mock import AsyncMock

    redis = AsyncMock()
    redis.set = AsyncMock()
    redis.lrange = AsyncMock(return_value=[])
    manager = SessionManager(redis)

    meta = json.dumps({
        "session_id": "sess-resume",
        "customer_id": None,
        "channel_type": "web",
        "current_phase": "agent",
        "sub_phase": "agent:on_hold",
        "end_reason": None,
        "vip_level": "普通",
        "card_types": [],
        "risk_tolerance": "R2",
        "turn_count": 0,
        "last_intent": None,
        "last_entities": [],
        "confidence_history": [],
        "low_confidence_streak": 0,
        "human_request_score": 0,
        "agent_id": None,
        "transfer_reason": None,
        "transfer_summary": None,
        "created_at": datetime.now().isoformat(),
        "last_active_at": datetime.now().isoformat(),
        "version": 1,
    }, ensure_ascii=False)
    redis.get = AsyncMock(return_value=meta)

    result = await manager.transition_phase(
        "sess-resume",
        SessionPhase.AGENT,
        new_sub_phase=SessionSubPhase.AG_ACTIVE,
        reason="agent_resume",
    )
    assert result.sub_phase == SessionSubPhase.AG_ACTIVE
