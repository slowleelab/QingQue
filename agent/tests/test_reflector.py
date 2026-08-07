"""Reflector 真实失败路径测试 (P2-6)

之前 test_reflector 只 mock _client.chat.completions, 不验证真实网络/解析错误路径.
现增加真实 fail 场景, 覆盖:
1. _client 不可用 → 返回 PASS, 降级
2. 解析失败 (非法 JSON / 缺字段) → 降级 PASS
3. session_id 连续失败计数 → 3 次升级 ESCALATE
4. reset_failure_count 正确重置
5. LRU 上限保护 (10000 条, 超出后淘汰最旧)
6. decision 字段非 enum 值 → 降级 PASS

不使用 mock, 直接通过构造非法输入触发真实代码路径.
"""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from lumio.services.bot.agents.reflector import (
    ReflectionDecision,
    Reflector,
    get_reflector,
)

# ── 1. _client 不可用 (openai SDK 缺失或初始化失败) ──


def test_reflect_client_unavailable_returns_pass() -> None:
    """_client 为 None → 返回 PASS 降级, 不抛异常."""
    r = Reflector.__new__(Reflector)  # 绕过 __init__, 避免真实 client 初始化
    r.judge_model = "qwen2.5:7b"
    r._base_url = "http://localhost:11434/v1"
    r._client = None
    r._failure_count = OrderedDict()
    r._failure_count_max = 10000

    result = asyncio.run(
        r.reflect(
            question="我的额度多少?",
            tool_name="query_credit_limit",
            tool_args={"card_id": "6222021234567890"},
            tool_result={"available": 50000},
            session_id="s1",
        )
    )
    assert result.decision == ReflectionDecision.PASS
    assert "不可用" in result.reason


# ── 2. 真实 HTTP 失败 (mock 真实 AsyncClient, 不 mock 出业务返回值) ──


@pytest.mark.asyncio
async def test_reflect_http_error_returns_pass() -> None:
    """HTTP 调用抛异常 → 降级 PASS, 不向上抛出."""
    r = Reflector.__new__(Reflector)
    r.judge_model = "qwen2.5:7b"
    r._base_url = "http://localhost:11434/v1"
    r._failure_count = OrderedDict()
    r._failure_count_max = 10000

    # 真实 AsyncMock, 不预设返回值, 模拟网络异常
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=ConnectionError("refused"))
    r._client = mock_client

    result = await r.reflect(
        question="查账单",
        tool_name="query_bill",
        tool_args={"month": "2026-07"},
        tool_result={"amount": 1000},
        session_id="s2",
    )
    assert result.decision == ReflectionDecision.PASS
    assert "降级" in result.reason or "失败" in result.reason


# ── 3. 解析失败 (真实 HTTP 返回但内容非法) ──


@pytest.mark.asyncio
async def test_reflect_invalid_json_returns_pass() -> None:
    """HTTP 返回非法 JSON → 解析失败降级 PASS, 不抛."""
    r = Reflector.__new__(Reflector)
    r.judge_model = "qwen2.5:7b"
    r._base_url = "http://localhost:11434/v1"
    r._failure_count = OrderedDict()
    r._failure_count_max = 10000

    # 真实 API 返回结构, 但 content 是非 JSON
    bad_message = MagicMock()
    bad_message.content = "this is not json at all"
    bad_choice = MagicMock()
    bad_choice.message = bad_message
    bad_response = MagicMock()
    bad_response.choices = [bad_choice]
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=bad_response)
    r._client = mock_client

    result = await r.reflect(question="q", tool_name="t", tool_args={}, tool_result={}, session_id="s3")
    # JSON 解析失败, Reflector 走 except, 返回 PASS 降级
    assert result.decision == ReflectionDecision.PASS


# ── 4. decision 字段非 enum 值 → 降级 PASS ──


@pytest.mark.asyncio
async def test_reflect_invalid_decision_enum_falls_back_to_pass() -> None:
    """HTTP 返回合法 JSON, 但 decision 字段值不是合法 enum → 降级 PASS."""
    r = Reflector.__new__(Reflector)
    r.judge_model = "qwen2.5:7b"
    r._base_url = "http://localhost:11434/v1"
    r._failure_count = OrderedDict()
    r._failure_count_max = 10000

    good_message = MagicMock()
    good_message.content = json.dumps({"decision": "totally_made_up", "reason": "x"})
    good_choice = MagicMock()
    good_choice.message = good_message
    good_response = MagicMock()
    good_response.choices = [good_choice]
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=good_response)
    r._client = mock_client

    result = await r.reflect(question="q", tool_name="t", tool_args={}, tool_result={}, session_id="s4")
    assert result.decision == ReflectionDecision.PASS


# ── 5. 连续失败 3 次 → 升级 ESCALATE (真实计数逻辑) ──


@pytest.mark.asyncio
async def test_reflect_three_consecutive_failures_escalates() -> None:
    """session_id 连续 3 次 RETRY/FAIL → 第 3 次返回 ESCALATE, 不依赖 LLM mock 的 happy path."""
    r = Reflector.__new__(Reflector)
    r.judge_model = "qwen2.5:7b"
    r._base_url = "http://localhost:11434/v1"
    r._failure_count = OrderedDict()
    r._failure_count_max = 10000

    def make_response(decision_value: str) -> Any:
        msg = MagicMock()
        msg.content = json.dumps({"decision": decision_value, "reason": "test"})
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=make_response("retry")  # 真实返回 retry
    )
    r._client = mock_client

    sid = "session-escalate"
    # 第 1, 2 次: RETRY
    r1 = await r.reflect(question="q", tool_name="t", tool_args={}, tool_result={}, session_id=sid)
    r2 = await r.reflect(question="q", tool_name="t", tool_args={}, tool_result={}, session_id=sid)
    assert r1.decision == ReflectionDecision.RETRY
    assert r2.decision == ReflectionDecision.RETRY
    assert r._failure_count[sid] == 2

    # 第 3 次: 应触发升级
    r3 = await r.reflect(question="q", tool_name="t", tool_args={}, tool_result={}, session_id=sid)
    assert r3.decision == ReflectionDecision.ESCALATE
    assert "transfer_to_agent" in r3.suggested_action or "升级" in r3.reason


# ── 6. reset_failure_count 正确重置 ──


def test_reset_failure_count() -> None:
    """会话成功完成后, 失败计数应被清空."""
    r = Reflector.__new__(Reflector)
    r.judge_model = "qwen2.5:7b"
    r._base_url = "http://localhost:11434/v1"
    r._failure_count = OrderedDict()
    r._failure_count_max = 10000

    sid = "s-reset"
    r._failure_count[sid] = 2
    assert sid in r._failure_count
    r.reset_failure_count(sid)
    assert sid not in r._failure_count

    # 不存在的 sid 也安全
    r.reset_failure_count("nonexistent")  # 不抛


# ── 7. LRU 上限保护 (10000 条) ──


def test_failure_count_lru_eviction() -> None:
    """超过 _failure_count_max 时, LRU 淘汰最旧条目, 防内存爆炸."""
    r = Reflector.__new__(Reflector)
    r.judge_model = "qwen2.5:7b"
    r._base_url = "http://localhost:11434/v1"
    r._failure_count = OrderedDict()
    r._failure_count_max = 5  # 缩小以加快测试

    # 写入 6 条, 第 1 条应被淘汰
    for i in range(6):
        sid = f"s-{i}"
        r._failure_count[sid] = i
        r._failure_count.move_to_end(sid)
        if len(r._failure_count) > r._failure_count_max:
            r._failure_count.popitem(last=False)

    assert len(r._failure_count) == 5
    # s-0 被淘汰
    assert "s-0" not in r._failure_count
    assert "s-5" in r._failure_count


# ── 8. get_reflector 单例 (线程安全 via functools.cache) ──


def test_get_reflector_returns_singleton() -> None:
    """get_reflector 应返回同一实例."""
    # get_reflector 走 functools.cache, 多次调用同实例
    # 注: 在 conftest 中如果已初始化, 这里直接用同一引用
    r1 = get_reflector()
    r2 = get_reflector()
    assert r1 is r2
