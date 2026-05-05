"""CAS 乐观锁状态管理器测试

对应设计文档 §3.2 统一状态层，覆盖:
- 读取状态
- CAS 写入（成功/冲突/重试）
- 字段级合并规则（6 类）
- 状态快照
- 状态初始化
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from smartcs.services.common.state_manager import StateManager


# ── Fixtures ──


@pytest.fixture
def mock_redis() -> AsyncMock:
    """模拟 Redis 客户端"""
    redis = AsyncMock()
    redis.script_load = AsyncMock(return_value="fake_sha")
    return redis


@pytest.fixture
def state_manager(mock_redis: AsyncMock) -> StateManager:
    """创建 StateManager 实例"""
    return StateManager(redis=mock_redis, ttl=1800)


def _make_state(version: int = 1, **overrides: Any) -> dict[str, Any]:
    """构造测试状态字典"""
    state: dict[str, Any] = {
        "version": version,
        "last_writer": "init",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "risk_pending_audit": False,
        "intent_stack": [],
        "entity_pool": [],
        "emotion_vector": None,
        "suppress_flag": False,
        "node_position": "",
    }
    state.update(overrides)
    return state


# ── TestStateManagerRead ──


class TestStateManagerRead:
    """读取状态测试"""

    async def test_read_state_returns_dict(self, state_manager: StateManager, mock_redis: AsyncMock) -> None:
        """读取已存在的状态返回字典"""
        state = _make_state(version=3, intent_stack=["faq"])
        mock_redis.get = AsyncMock(return_value=json.dumps(state))

        result = await state_manager.read_state("sess-001")

        assert result is not None
        assert result["version"] == 3
        assert result["intent_stack"] == ["faq"]
        mock_redis.get.assert_awaited_once_with("smartcs:state:sess-001")

    async def test_read_state_returns_none_when_missing(self, state_manager: StateManager, mock_redis: AsyncMock) -> None:
        """读取不存在的状态返回 None"""
        mock_redis.get = AsyncMock(return_value=None)

        result = await state_manager.read_state("sess-missing")

        assert result is None


# ── TestStateManagerCASWrite ──


class TestStateManagerCASWrite:
    """CAS 写入测试"""

    async def test_cas_write_success_on_version_match(
        self, state_manager: StateManager, mock_redis: AsyncMock
    ) -> None:
        """版本匹配时 CAS 写入成功"""
        current_state = _make_state(version=2)
        mock_redis.get = AsyncMock(return_value=json.dumps(current_state))

        # 模拟 evalsha 返回成功
        cas_result = json.dumps({"ok": True, "new_version": 3}).encode()
        mock_redis.evalsha = AsyncMock(return_value=cas_result)

        result = await state_manager.cas_write(
            "sess-001",
            expected_version=2,
            patches={"node_position": "classify"},
            writer="e1",
        )

        assert result["ok"] is True
        assert result["new_version"] == 3

    async def test_cas_write_conflict_on_version_mismatch(
        self, state_manager: StateManager, mock_redis: AsyncMock
    ) -> None:
        """版本不匹配时 CAS 写入返回冲突"""
        current_state = _make_state(version=5)
        mock_redis.get = AsyncMock(return_value=json.dumps(current_state))

        # 模拟 evalsha 返回冲突
        cas_result = json.dumps({"ok": False, "current_version": 5, "reason": "version_mismatch"}).encode()
        mock_redis.evalsha = AsyncMock(return_value=cas_result)

        result = await state_manager.cas_write(
            "sess-001",
            expected_version=3,
            patches={"node_position": "classify"},
            writer="e1",
        )

        assert result["ok"] is False
        assert result["current_version"] == 5

    async def test_cas_write_retries_on_conflict(
        self, state_manager: StateManager, mock_redis: AsyncMock
    ) -> None:
        """版本冲突时自动重试"""
        current_state = _make_state(version=5)
        mock_redis.get = AsyncMock(return_value=json.dumps(current_state))

        # 第一次冲突，第二次成功
        conflict_result = json.dumps({"ok": False, "current_version": 5, "reason": "version_mismatch"}).encode()
        success_result = json.dumps({"ok": True, "new_version": 6}).encode()
        mock_redis.evalsha = AsyncMock(side_effect=[conflict_result, success_result])

        result = await state_manager.cas_write(
            "sess-001",
            expected_version=3,
            patches={"node_position": "classify"},
            writer="e1",
            max_retries=1,
        )

        assert result["ok"] is True
        assert result["new_version"] == 6
        assert mock_redis.evalsha.await_count == 2

    async def test_cas_write_returns_not_found_when_state_missing(
        self, state_manager: StateManager, mock_redis: AsyncMock
    ) -> None:
        """状态不存在时返回 not_found"""
        mock_redis.get = AsyncMock(return_value=None)

        result = await state_manager.cas_write(
            "sess-missing",
            expected_version=1,
            patches={"node_position": "classify"},
            writer="e1",
        )

        assert result["ok"] is False
        assert result.get("reason") == "not_found"


# ── TestStateManagerMergeRules ──


class TestStateManagerMergeRules:
    """字段级合并规则测试"""

    def test_risk_instruction_full_overwrite(self, state_manager: StateManager) -> None:
        """风控指令 (risk_pending_audit) 全量覆写"""
        current = _make_state(risk_pending_audit=False)
        patches = {"risk_pending_audit": True}

        result = state_manager._apply_merge_rules(current, patches)

        assert result["risk_pending_audit"] is True

    def test_node_position_full_overwrite(self, state_manager: StateManager) -> None:
        """节点位置 (node_position) 全量覆写"""
        current = _make_state(node_position="classify")
        patches = {"node_position": "retrieve"}

        result = state_manager._apply_merge_rules(current, patches)

        assert result["node_position"] == "retrieve"

    def test_intent_stack_incremental_merge(self, state_manager: StateManager) -> None:
        """意图栈 (intent_stack) 增量合并：追加 + 去重"""
        current = _make_state(intent_stack=["faq", "bill_query"])
        patches = {"intent_stack": ["card_loss", "faq"]}  # faq 已存在，应去重

        result = state_manager._apply_merge_rules(current, patches)

        assert result["intent_stack"] == ["faq", "bill_query", "card_loss"]

    def test_intent_stack_incremental_merge_empty_current(self, state_manager: StateManager) -> None:
        """意图栈增量合并：当前为空列表"""
        current = _make_state(intent_stack=[])
        patches = {"intent_stack": ["faq", "complaint"]}

        result = state_manager._apply_merge_rules(current, patches)

        assert result["intent_stack"] == ["faq", "complaint"]

    def test_entity_pool_incremental_merge_add_new(self, state_manager: StateManager) -> None:
        """实体池 (entity_pool) 增量合并：新增实体"""
        current = _make_state(
            entity_pool=[
                {"entity_type": "card_number", "value": "6225****1234", "confidence": 0.9},
            ]
        )
        patches = {
            "entity_pool": [
                {"entity_type": "amount", "value": "5000", "confidence": 0.8},
            ]
        }

        result = state_manager._apply_merge_rules(current, patches)

        assert len(result["entity_pool"]) == 2
        types = [e["entity_type"] for e in result["entity_pool"]]
        assert "card_number" in types
        assert "amount" in types

    def test_entity_pool_incremental_merge_update_existing(self, state_manager: StateManager) -> None:
        """实体池增量合并：更新已有实体（按 entity_type+value 匹配）"""
        current = _make_state(
            entity_pool=[
                {"entity_type": "card_number", "value": "6225****1234", "confidence": 0.9},
            ]
        )
        patches = {
            "entity_pool": [
                {"entity_type": "card_number", "value": "6225****1234", "confidence": 0.95},
            ]
        }

        result = state_manager._apply_merge_rules(current, patches)

        assert len(result["entity_pool"]) == 1
        assert result["entity_pool"][0]["confidence"] == 0.95

    def test_emotion_vector_time_window_replace(self, state_manager: StateManager) -> None:
        """情绪向量 (emotion_vector) 时间窗口替换：新值覆盖旧值"""
        current = _make_state(
            emotion_vector={"label": "neutral", "score": 0.5, "updated_at": "2026-01-01T00:00:00"}
        )
        patches = {
            "emotion_vector": {"label": "angry", "score": 0.9, "updated_at": "2026-01-01T00:01:00"}
        }

        result = state_manager._apply_merge_rules(current, patches)

        assert result["emotion_vector"]["label"] == "angry"
        assert result["emotion_vector"]["score"] == 0.9

    def test_suppress_flag_one_way_gate_false_to_true(self, state_manager: StateManager) -> None:
        """suppress_flag 单向门：false→true 允许"""
        current = _make_state(suppress_flag=False)
        patches = {"suppress_flag": True}

        result = state_manager._apply_merge_rules(current, patches)

        assert result["suppress_flag"] is True

    def test_suppress_flag_one_way_gate_cannot_unset(self, state_manager: StateManager) -> None:
        """suppress_flag 单向门：true→false 被阻止"""
        current = _make_state(suppress_flag=True)
        patches = {"suppress_flag": False}

        result = state_manager._apply_merge_rules(current, patches)

        # 不应出现在 adjusted patches 中（被单向门阻止）
        assert "suppress_flag" not in result

    def test_suppress_flag_same_value_allowed(self, state_manager: StateManager) -> None:
        """suppress_flag 单向门：相同值允许（幂等）"""
        current = _make_state(suppress_flag=True)
        patches = {"suppress_flag": True}

        result = state_manager._apply_merge_rules(current, patches)

        assert result["suppress_flag"] is True

    def test_merge_rules_mixed_fields(self, state_manager: StateManager) -> None:
        """多个字段同时更新时各自按规则合并"""
        current = _make_state(
            intent_stack=["faq"],
            entity_pool=[{"entity_type": "card", "value": "gold", "confidence": 0.8}],
            suppress_flag=False,
            node_position="classify",
        )
        patches = {
            "intent_stack": ["bill_query"],
            "entity_pool": [{"entity_type": "card", "value": "gold", "confidence": 0.95}],
            "suppress_flag": True,
            "node_position": "retrieve",
            "risk_pending_audit": True,
        }

        result = state_manager._apply_merge_rules(current, patches)

        assert result["intent_stack"] == ["faq", "bill_query"]
        assert len(result["entity_pool"]) == 1
        assert result["entity_pool"][0]["confidence"] == 0.95
        assert result["suppress_flag"] is True
        assert result["node_position"] == "retrieve"
        assert result["risk_pending_audit"] is True


# ── TestStateManagerSnapshot ──


class TestStateManagerSnapshot:
    """状态快照测试"""

    async def test_get_snapshot_returns_versioned_state(
        self, state_manager: StateManager, mock_redis: AsyncMock
    ) -> None:
        """获取快照包含版本号"""
        state = _make_state(version=7, node_position="generate")
        mock_redis.get = AsyncMock(return_value=json.dumps(state))

        result = await state_manager.get_snapshot("sess-001")

        assert result is not None
        assert result["version"] == 7
        assert result["node_position"] == "generate"

    async def test_get_snapshot_returns_none_when_missing(
        self, state_manager: StateManager, mock_redis: AsyncMock
    ) -> None:
        """获取不存在的快照返回 None"""
        mock_redis.get = AsyncMock(return_value=None)

        result = await state_manager.get_snapshot("sess-missing")

        assert result is None


# ── TestStateManagerInit ──


class TestStateManagerInit:
    """状态初始化测试"""

    async def test_init_state_creates_with_defaults(
        self, state_manager: StateManager, mock_redis: AsyncMock
    ) -> None:
        """初始化状态使用默认值"""
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock(return_value=True)

        result = await state_manager.init_state("sess-new", {})

        assert result["version"] == 1
        assert result["risk_pending_audit"] is False
        assert result["intent_stack"] == []
        assert result["entity_pool"] == []
        assert result["emotion_vector"] is None
        assert result["suppress_flag"] is False
        assert result["node_position"] == ""
        assert result["last_writer"] == "init"

    async def test_init_state_merges_initial_data(
        self, state_manager: StateManager, mock_redis: AsyncMock
    ) -> None:
        """初始化状态合并自定义数据"""
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock(return_value=True)

        result = await state_manager.init_state(
            "sess-new",
            {"customer_id": "cust-001", "channel_type": "phone", "intent_stack": ["faq"]},
        )

        assert result["customer_id"] == "cust-001"
        assert result["channel_type"] == "phone"
        assert result["intent_stack"] == ["faq"]
        # 默认值仍然存在
        assert result["version"] == 1
        assert result["suppress_flag"] is False

    async def test_init_state_idempotent_when_exists(
        self, state_manager: StateManager, mock_redis: AsyncMock
    ) -> None:
        """状态已存在时幂等返回当前状态"""
        existing = _make_state(version=5, customer_id="cust-001")
        # 第一次 get 返回已有状态
        mock_redis.get = AsyncMock(return_value=json.dumps(existing))

        result = await state_manager.init_state("sess-exists", {"customer_id": "cust-002"})

        assert result["version"] == 5
        assert result["customer_id"] == "cust-001"  # 不覆盖
        # 不应调用 SET
        mock_redis.set.assert_not_awaited()

    async def test_init_state_sets_ttl(
        self, state_manager: StateManager, mock_redis: AsyncMock
    ) -> None:
        """初始化状态设置 TTL"""
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock(return_value=True)

        await state_manager.init_state("sess-new", {})

        # 检查 SET 调用参数包含 ex=TTL
        call_args = mock_redis.set.call_args
        assert call_args.kwargs.get("ex") == 1800 or (len(call_args.args) > 2 and call_args.args[2] == 1800)


# ── TestCanSetSuppress ──


class TestCanSetSuppress:
    """suppress_flag 单向门检查单元测试"""

    def test_false_to_true_allowed(self, state_manager: StateManager) -> None:
        """false→true 允许"""
        assert state_manager._can_set_suppress(False, True) is True

    def test_true_to_false_blocked(self, state_manager: StateManager) -> None:
        """true→false 阻止"""
        assert state_manager._can_set_suppress(True, False) is False

    def test_false_to_false_allowed(self, state_manager: StateManager) -> None:
        """false→false 允许（幂等）"""
        assert state_manager._can_set_suppress(False, False) is True

    def test_true_to_true_allowed(self, state_manager: StateManager) -> None:
        """true→true 允许（幂等）"""
        assert state_manager._can_set_suppress(True, True) is True
