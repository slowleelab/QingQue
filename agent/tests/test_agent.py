"""LangGraph Agent 图单元测试"""

from __future__ import annotations

import pytest

from smartcs.services.bot.agent import (
    AgentState,
    _initial_state,
    _is_farewell,
    _is_greeting,
    classify_intent_node,
    supervisor_router,
    transfer_router,
)
from smartcs.services.common.classifier import IntentClassifier, RuleClassifier
from smartcs.shared.models import IntentLabel

# ── 辅助函数 ──


def test_initial_state() -> None:
    """初始状态应包含所有必要字段"""
    state = _initial_state("test-session", "你好")
    assert state["session_id"] == "test-session"
    assert state["user_input"] == "你好"
    assert state["intent"] is None
    assert state["domain"] == "fallback"
    assert state["should_transfer"] is False


def test_is_greeting() -> None:
    """问候语检测"""
    assert _is_greeting("你好") is True
    assert _is_greeting("您好") is True
    assert _is_greeting("hi") is True
    assert _is_greeting("查账单") is False


def test_is_farewell() -> None:
    """告别语检测"""
    assert _is_farewell("再见") is True
    assert _is_farewell("谢谢") is True
    assert _is_farewell("bye") is True
    assert _is_farewell("查账单") is False


# ── classify_intent 节点 ──


@pytest.mark.asyncio
async def test_classify_intent_node() -> None:
    """分类节点应更新 intent 和 domain"""
    rule = RuleClassifier()
    classifier = IntentClassifier(rule_classifier=rule, llm_classifier=None)
    state = _initial_state("test-session", "我要查账单")

    result = await classify_intent_node(state, classifier=classifier)

    assert result["intent"] is not None
    assert result["intent"].primary_intent == IntentLabel.BILL_QUERY
    assert result["domain"] == "knowledge"
    assert result["classify_source"] == "rule"


# ── supervisor_router 条件边 ──


def test_supervisor_routes_knowledge() -> None:
    """knowledge 域应路由到 knowledge_agent"""
    state = AgentState(domain="knowledge")
    assert supervisor_router(state) == "knowledge_agent"


def test_supervisor_routes_business() -> None:
    """business 域应路由到 business_agent"""
    state = AgentState(domain="business")
    assert supervisor_router(state) == "business_agent"


def test_supervisor_routes_fallback() -> None:
    """fallback 域应路由到 fallback_agent"""
    state = AgentState(domain="fallback")
    assert supervisor_router(state) == "fallback_agent"


# ── transfer_router 条件边 ──


def test_transfer_router_transfer() -> None:
    """should_transfer=True 应路由到 transfer"""
    state = AgentState(should_transfer=True)
    assert transfer_router(state) == "transfer"


def test_transfer_router_respond() -> None:
    """should_transfer=False 应路由到 respond"""
    state = AgentState(should_transfer=False)
    assert transfer_router(state) == "respond"
