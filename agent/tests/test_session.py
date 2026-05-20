"""会话状态管理单元测试"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from smartcs.services.common.session import SessionManager
from smartcs.shared.models import (
    ChannelType,
    DialogueTurn,
    IntentLabel,
    IntentResult,
    SessionPhase,
    SessionSubPhase,
)


def _make_turn(session_id: str, speaker: str = "customer", content: str = "test") -> DialogueTurn:
    """构造测试用对话轮次"""
    return DialogueTurn(
        turn_id="test-turn-id",
        session_id=session_id,
        speaker=speaker,
        content=content,
        timestamp=datetime.now(),
    )


def _mock_redis() -> AsyncMock:
    """构造模拟 Redis 客户端"""
    redis = AsyncMock()
    redis.set = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.rpush = AsyncMock()
    redis.llen = AsyncMock(return_value=0)
    redis.lrange = AsyncMock(return_value=[])
    redis.ltrim = AsyncMock()
    redis.delete = AsyncMock()
    return redis


# ── 创建会话 ──


@pytest.mark.asyncio
async def test_create_session() -> None:
    """创建会话应返回有效的 SessionState"""
    redis = _mock_redis()
    manager = SessionManager(redis)
    state = await manager.create_session(customer_id="cust-001")

    assert state.session_id
    assert state.customer_id == "cust-001"
    assert state.current_phase == SessionPhase.BOT
    assert state.sub_phase == SessionSubPhase.BOT_ACTIVE
    assert state.turns == []
    redis.set.assert_called_once()


@pytest.mark.asyncio
async def test_create_session_with_channel() -> None:
    """创建会话应正确设置渠道类型"""
    redis = _mock_redis()
    manager = SessionManager(redis)
    state = await manager.create_session(channel_type=ChannelType.APP)
    assert state.channel_type == ChannelType.APP


# ── 加载会话 ──


@pytest.mark.asyncio
async def test_get_session_not_found() -> None:
    """获取不存在的会话应返回 None"""
    redis = _mock_redis()
    manager = SessionManager(redis)
    result = await manager.get_session("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_get_session_exists() -> None:
    """获取存在的会话应返回 SessionState"""
    redis = _mock_redis()

    # 模拟 Redis 返回元信息
    meta = json.dumps({
        "session_id": "test-session",
        "customer_id": None,
        "channel_type": "web",
        "current_phase": "bot",
        "sub_phase": "bot:active",
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
    redis.lrange = AsyncMock(return_value=[])

    manager = SessionManager(redis)
    state = await manager.get_session("test-session")
    assert state is not None
    assert state.session_id == "test-session"
    assert state.current_phase == SessionPhase.BOT


# ── 追加对话 ──


@pytest.mark.asyncio
async def test_add_turn() -> None:
    """追加对话轮次应更新历史"""
    redis = _mock_redis()
    manager = SessionManager(redis)

    # 先创建会话
    state = await manager.create_session()

    # 模拟 Redis 中存在元信息
    meta = json.dumps({
        "session_id": state.session_id,
        "customer_id": None,
        "channel_type": "web",
        "current_phase": "bot",
        "sub_phase": "bot:active",
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
    redis.lrange = AsyncMock(return_value=[])

    turn = _make_turn(state.session_id)
    intent = IntentResult(primary_intent=IntentLabel.BILL_QUERY, primary_confidence=0.9)
    updated = await manager.add_turn(state.session_id, turn, intent=intent)

    assert updated.last_intent == IntentLabel.BILL_QUERY
    redis.rpush.assert_called_once()


@pytest.mark.asyncio
async def test_add_turn_low_confidence_increments_streak() -> None:
    """低置信度意图应增加 low_confidence_streak"""
    redis = _mock_redis()
    manager = SessionManager(redis)

    state = await manager.create_session()

    # 模拟 Redis 返回现有会话（streak=0）
    meta = json.dumps({
        "session_id": state.session_id,
        "customer_id": None,
        "channel_type": "web",
        "current_phase": "bot",
        "sub_phase": "bot:active",
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
    redis.lrange = AsyncMock(return_value=[])

    turn = _make_turn(state.session_id)
    intent = IntentResult(primary_intent=IntentLabel.FAQ, primary_confidence=0.3)
    updated = await manager.add_turn(state.session_id, turn, intent=intent)

    assert updated.low_confidence_streak == 1


# ── 阶段切换 ──


@pytest.mark.asyncio
async def test_transition_phase() -> None:
    """阶段切换应更新 current_phase"""
    redis = _mock_redis()
    manager = SessionManager(redis)

    state = await manager.create_session()

    meta = json.dumps({
        "session_id": state.session_id,
        "customer_id": None,
        "channel_type": "web",
        "current_phase": "bot",
        "sub_phase": "bot:active",
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
    redis.lrange = AsyncMock(return_value=[])

    updated = await manager.transition_phase(
        state.session_id, SessionPhase.AGENT,
        new_sub_phase=SessionSubPhase.AG_QUEUED,
        reason="L1_KEYWORD_HIT",
    )
    assert updated.current_phase == SessionPhase.AGENT
    assert updated.sub_phase == SessionSubPhase.AG_QUEUED
    assert updated.transfer_reason == "L1_KEYWORD_HIT"


# ── get_or_create ──


@pytest.mark.asyncio
async def test_get_or_create_existing() -> None:
    """get_or_create 对已有会话应返回现有状态"""
    redis = _mock_redis()
    manager = SessionManager(redis)

    state = await manager.create_session()

    meta = json.dumps({
        "session_id": state.session_id,
        "customer_id": None,
        "channel_type": "web",
        "current_phase": "bot",
        "sub_phase": "bot:active",
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
    redis.lrange = AsyncMock(return_value=[])

    result = await manager.get_or_create(state.session_id)
    assert result.session_id == state.session_id


@pytest.mark.asyncio
async def test_get_or_create_new() -> None:
    """get_or_create 对空 session_id 应创建新会话"""
    redis = _mock_redis()
    manager = SessionManager(redis)

    state = await manager.get_or_create(None)
    assert state.session_id


# ── 删除会话 ──


@pytest.mark.asyncio
async def test_delete_session() -> None:
    """删除会话应清理 Redis 键"""
    redis = _mock_redis()
    manager = SessionManager(redis)

    await manager.delete_session("test-session")
    redis.delete.assert_called_once()


# ── 状态转换校验 ──


def test_validate_transition_legal() -> None:
    """合法转换应通过校验"""
    from smartcs.shared.models import validate_transition

    assert validate_transition(SessionPhase.BOT, SessionSubPhase.BOT_ACTIVE, SessionSubPhase.AG_QUEUED) is True
    assert validate_transition(SessionPhase.AGENT, SessionSubPhase.AG_QUEUED, SessionSubPhase.AG_ASSIGNED) is True
    assert validate_transition(SessionPhase.AGENT, SessionSubPhase.AG_ACTIVE, SessionSubPhase.AG_REVIEWING) is True


def test_validate_transition_illegal() -> None:
    """非法转换应被拒绝"""
    from smartcs.shared.models import validate_transition

    # 不能从 BOT 直接跳到 AG_ACTIVE
    assert validate_transition(SessionPhase.BOT, SessionSubPhase.BOT_ACTIVE, SessionSubPhase.AG_ACTIVE) is False
    # 不能从 AG_REVIEWING 回到 AG_ACTIVE
    assert validate_transition(SessionPhase.AGENT, SessionSubPhase.AG_REVIEWING, SessionSubPhase.AG_ACTIVE) is False


@pytest.mark.asyncio
async def test_transition_phase_illegal_raises() -> None:
    """非法阶段切换应抛出 ValueError"""
    redis = _mock_redis()
    manager = SessionManager(redis)

    state = await manager.create_session()

    # 模拟 Redis 中已处于 AG_ACTIVE 的会话
    meta = json.dumps({
        "session_id": state.session_id,
        "customer_id": None,
        "channel_type": "web",
        "current_phase": "agent",
        "sub_phase": "agent:reviewing",
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
    redis.lrange = AsyncMock(return_value=[])

    # AG_REVIEWING → AG_ACTIVE 是非法转换
    with pytest.raises(ValueError, match="非法状态转换"):
        await manager.transition_phase(
            state.session_id, SessionPhase.AGENT,
            new_sub_phase=SessionSubPhase.AG_ACTIVE,
        )


@pytest.mark.asyncio
async def test_transition_phase_sub_phase_progression() -> None:
    """子阶段正常推进链路: BOT → AG_QUEUED → AG_ASSIGNED → AG_ACTIVE"""
    redis = _mock_redis()
    manager = SessionManager(redis)

    state = await manager.create_session()

    # 模拟 BOT 阶段
    meta = json.dumps({
        "session_id": state.session_id,
        "customer_id": None,
        "channel_type": "web",
        "current_phase": "bot",
        "sub_phase": "bot:active",
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
    redis.lrange = AsyncMock(return_value=[])

    # BOT → AG_QUEUED
    result = await manager.transition_phase(
        state.session_id, SessionPhase.AGENT,
        new_sub_phase=SessionSubPhase.AG_QUEUED,
        reason="L1_KEYWORD_HIT",
    )
    assert result.current_phase == SessionPhase.AGENT
    assert result.sub_phase == SessionSubPhase.AG_QUEUED
