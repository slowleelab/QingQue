"""CAS 乐观锁状态管理器

对应设计文档 §3.2 统一状态层。使用 Redis Lua 脚本实现
Compare-And-Swap 原子写入，配合字段级合并规则保证
多执行器并发写入时的数据一致性。

合并规则（对应文档 §3.2 覆写规则表）:
- 风控指令 (risk_pending_audit): 全量覆写，风控优先级最高
- 意图栈 (intent_stack): 增量合并（压栈/弹栈操作），去重
- 实体池 (entity_pool): 增量合并（新增/更新实体），按 entity_type+value 去重
- 情绪向量 (emotion_vector): 时间窗口替换（新值覆盖旧值）
- Suppress_Flag (suppress_flag): 单向门 false→true
- Node_Position (node_position): 全量覆写
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# ── CAS Lua 脚本 ──

_CAS_WRITE_SCRIPT = """
-- CAS 乐观锁写入
-- KEYS[1] = state key
-- ARGV[1] = expected_version
-- ARGV[2] = patch JSON
-- ARGV[3] = writer id
-- ARGV[4] = TTL seconds
--
-- 返回:
--   成功: {ok: true, new_version: N}
--   冲突: {ok: false, current_version: N}

local key = KEYS[1]
local expected_version = tonumber(ARGV[1])
local patch_json = ARGV[2]
local writer = ARGV[3]
local ttl = tonumber(ARGV[4])

local raw = redis.call('GET', key)
if raw == false then
    return cjson.encode({ok = false, current_version = 0, reason = 'not_found'})
end

local current = cjson.decode(raw)
if current.version ~= expected_version then
    return cjson.encode({ok = false, current_version = current.version, reason = 'version_mismatch'})
end

-- 版本递增 + 元数据更新
current.version = current.version + 1
current.last_writer = writer
current.updated_at = ARGV[5]

-- 字段级合并：将 patch 中的字段合并到 current
local patches = cjson.decode(patch_json)
for k, v in pairs(patches) do
    current[k] = v
end

redis.call('SET', key, cjson.encode(current), 'EX', ttl)
return cjson.encode({ok = true, new_version = current.version})
"""

# 需要增量合并的字段（在 Python 侧处理，Lua 侧不感知）
_INCREMENTAL_FIELDS = {"intent_stack", "entity_pool"}

# 需要单向门检查的字段
_ONE_WAY_GATE_FIELDS = {"suppress_flag"}

# 全量覆写字段（直接覆写，无需特殊处理）
_FULL_OVERWRITE_FIELDS = {"risk_pending_audit", "node_position"}


class StateManager:
    """CAS 乐观锁状态管理器

    对应设计文档 §3.2 统一状态层。
    """

    STATE_PREFIX = "smartcs:state"

    def __init__(self, redis: Redis, ttl: int = 1800) -> None:
        self._redis = redis
        self._ttl = ttl  # 30 分钟，对应文档 §3.2 持久化: 会话 TTL
        self._script_sha: str | None = None

    def _state_key(self, session_id: str) -> str:
        """构造 Redis Key"""
        return f"{self.STATE_PREFIX}:{session_id}"

    async def _ensure_script(self) -> str:
        """加载 Lua 脚本并缓存 SHA"""
        if self._script_sha is None:
            self._script_sha = await self._redis.script_load(_CAS_WRITE_SCRIPT)
        return self._script_sha

    async def read_state(self, session_id: str) -> dict[str, Any] | None:
        """读取状态对象

        Args:
            session_id: 会话 ID

        Returns:
            状态字典，不存在时返回 None
        """
        raw = await self._redis.get(self._state_key(session_id))
        if raw is None:
            return None
        return json.loads(raw)

    async def cas_write(
        self,
        session_id: str,
        expected_version: int,
        patches: dict[str, Any],
        *,
        writer: str = "",
        max_retries: int = 1,
    ) -> dict[str, Any]:
        """CAS 写入状态

        先根据当前状态应用合并规则调整 patches，再通过 Lua 脚本原子写入。
        版本冲突时自动重试（重新读取、重新应用合并规则）。

        Args:
            session_id: 会话 ID
            expected_version: 期望的版本号
            patches: 待合并的字段补丁
            writer: 写入者标识（对应文档 §3.2 last_writer）
            max_retries: 版本冲突时的最大重试次数

        Returns:
            {"ok": True, "new_version": int} 或 {"ok": False, "current_version": int}
        """
        transformed_patches = patches
        current_version = expected_version

        for attempt in range(max_retries + 1):
            # 读取当前状态，应用合并规则
            current = await self.read_state(session_id)
            if current is None:
                return {"ok": False, "current_version": 0, "reason": "not_found"}

            transformed_patches = self._apply_merge_rules(current, patches)

            # 构造 Lua 脚本参数
            sha = await self._ensure_script()
            now_iso = datetime.now(UTC).isoformat()
            patch_json = json.dumps(transformed_patches, ensure_ascii=False, default=str)

            result = await self._redis.evalsha(
                sha,
                1,  # numkeys
                self._state_key(session_id),  # KEYS[1]
                str(current_version),  # ARGV[1] expected_version
                patch_json,  # ARGV[2] patches
                writer,  # ARGV[3] writer
                str(self._ttl),  # ARGV[4] TTL
                now_iso,  # ARGV[5] updated_at
            )

            # evalsha 返回的可能是 dict / JSON string / bytes
            if isinstance(result, bytes):
                result = json.loads(result.decode())
            elif isinstance(result, str):
                result = json.loads(result)

            if result.get("ok"):
                logger.debug(
                    "CAS 写入成功: session=%s version=%d→%d writer=%s",
                    session_id,
                    current_version,
                    result["new_version"],
                    writer,
                )
                return {"ok": True, "new_version": result["new_version"]}

            # 版本冲突
            current_version = result.get("current_version", current_version)
            logger.debug(
                "CAS 写入冲突: session=%s expected=%d actual=%d attempt=%d/%d",
                session_id,
                expected_version,
                current_version,
                attempt + 1,
                max_retries + 1,
            )

            if attempt < max_retries:
                # 重试：使用最新版本号
                continue

        return {"ok": False, "current_version": current_version}

    async def get_snapshot(self, session_id: str) -> dict[str, Any] | None:
        """获取状态快照（含版本号）

        Args:
            session_id: 会话 ID

        Returns:
            包含 version 字段的状态字典，不存在时返回 None
        """
        return await self.read_state(session_id)

    async def init_state(self, session_id: str, initial_data: dict[str, Any]) -> dict[str, Any]:
        """初始化状态对象

        如果状态已存在则不覆盖（幂等），返回当前状态。

        Args:
            session_id: 会话 ID
            initial_data: 初始数据

        Returns:
            创建或已有的状态字典
        """
        existing = await self.read_state(session_id)
        if existing is not None:
            return existing

        now_iso = datetime.now(UTC).isoformat()
        state: dict[str, Any] = {
            "version": 1,
            "last_writer": "init",
            "created_at": now_iso,
            "updated_at": now_iso,
            # 编排层扩展字段默认值
            "risk_pending_audit": False,
            "intent_stack": [],
            "entity_pool": [],
            "emotion_vector": None,
            "suppress_flag": False,
            "node_position": "",
        }
        state.update(initial_data)

        state_json = json.dumps(state, ensure_ascii=False, default=str)
        # 使用 SET NX 保证幂等（不存在时才设置）
        was_set = await self._redis.set(
            self._state_key(session_id),
            state_json,
            ex=self._ttl,
            nx=True,
        )

        if was_set:
            logger.info("状态初始化成功: session=%s", session_id)
            return state

        # 并发初始化，读取已有状态
        logger.debug("状态已存在，跳过初始化: session=%s", session_id)
        existing = await self.read_state(session_id)
        return existing if existing is not None else state

    def _apply_merge_rules(self, current: dict, patches: dict) -> dict:
        """根据覆写规则调整 patches

        合并规则（对应设计文档 §3.2 覆写规则表）:
        - 风控指令 / 节点位置: 全量覆写（直接传递）
        - 意图栈: 增量合并，当前值 + 新值（去重）
        - 实体池: 增量合并，新增或按 entity_type+value 更新
        - 情绪向量: 时间窗口替换（直接传递）
        - Suppress_Flag: 单向门，只能 false→true

        Args:
            current: 当前状态
            patches: 原始补丁

        Returns:
            调整后的补丁（已符合合并规则）
        """
        adjusted: dict[str, Any] = {}

        for field, value in patches.items():
            # H1: suppress_flag duration 到期后强制清除
            if field == "suppress_flag" and value is False and "suppress_force_clear" in patches:
                adjusted[field] = value
                continue

            if field == "suppress_force_clear":
                # 辅助字段，不写入状态
                continue

            if field in _ONE_WAY_GATE_FIELDS:
                # 单向门字段：检查状态转换合法性
                if field == "suppress_flag":
                    current_val = current.get(field, False)
                    if self._can_set_suppress(current_val, value):
                        adjusted[field] = value
                    else:
                        logger.debug("suppress_flag 单向门阻止: current=%s new=%s", current_val, value)
                else:
                    adjusted[field] = value

            elif field == "intent_stack":
                # 增量合并：当前列表 + 新增项（去重）
                current_list: list = current.get(field, [])
                if isinstance(value, list):
                    # 去重追加
                    existing_set = set(str(item) for item in current_list)
                    for item in value:
                        if str(item) not in existing_set:
                            current_list.append(item)
                            existing_set.add(str(item))
                adjusted[field] = current_list

            elif field == "entity_pool":
                # 增量合并：新增/更新实体（按 entity_type+value 去重）
                current_entities: list[dict] = current.get(field, [])
                if isinstance(value, list):
                    # 构建实体索引
                    entity_index: dict[str, dict] = {}
                    for entity in current_entities:
                        key = f"{entity.get('entity_type', '')}:{entity.get('value', '')}"
                        entity_index[key] = entity

                    for entity in value:
                        if isinstance(entity, dict):
                            key = f"{entity.get('entity_type', '')}:{entity.get('value', '')}"
                            entity_index[key] = entity  # 新增或更新

                    adjusted[field] = list(entity_index.values())
                else:
                    adjusted[field] = value

            elif field == "emotion_vector":
                # 时间窗口替换：新值覆盖旧值（emotion_vector 始终取最新）
                # 如果旧值存在且比新值更近，则保留旧值
                current_ev = current.get("emotion_vector")
                if current_ev and isinstance(current_ev, dict) and isinstance(value, dict):
                    current_time = current_ev.get("updated_at", "")
                    new_time = value.get("updated_at", "")
                    if current_time and new_time and current_time > new_time:
                        # 当前值更新，保留
                        adjusted[field] = current_ev
                    else:
                        adjusted[field] = value
                else:
                    adjusted[field] = value

            else:
                # 全量覆写（risk_pending_audit, node_position 等）
                adjusted[field] = value

        return adjusted

    def _can_set_suppress(self, current: bool, new: bool) -> bool:
        """suppress_flag 单向门检查：只能从 false → true

        例外: 当 patch 中包含 suppress_force_clear=True 时，
        允许 true→false（用于 duration 到期后自动清除）。

        Args:
            current: 当前值
            new: 新值

        Returns:
            True 允许设置，False 阻止
        """
        # 只允许 false→true，不允许 true→false
        return not (current is True and new is False)
