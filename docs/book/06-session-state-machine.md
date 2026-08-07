---
title: "第 6 章: 会话状态机"
chapter: 6
part: "核心代码"
difficulty: "高级"
reading_time: "22 分钟"
prerequisites: ["第 1 章: 整体架构", "第 3 章: Bot 自助问答"]
code_references:
  - "agent/lumio/services/common/session.py"
  - "agent/lumio/services/common/session_timeout.py"
  - "agent/lumio/shared/models.py"
  - "agent/lumio/shared/orm_models.py"
last_updated: "2026-08-05"
summary: "3 phase × 7 sub-state 状态机 + CAS Lua 原子控制 + 字段级合并规则 + ZSET 超时队列 + PG 异步落库."
tags: ["状态机", "cas", "lua", "zset", "redis", "会话"]
---

# 第 6 章: 会话状态机

> 本章深入 Lumio 会话状态机. 银行客服一次通话从客户拨入到结束, 涉及 3 phase × 6 sub-state 的状态组合 — 怎么保证多实例并发下不丢转换, 怎么 5 秒检测超时, 怎么保证 5-7 年审计留存, 是本章核心. 看完本章你会理解: 状态机怎么设计才能既灵活又安全, CAS Lua 怎么原子控制并发, ZSET 怎么替代 asyncio.Task 做分布式超时, PostgreSQL 怎么异步落库.

## 6.1 状态机全景

**先讲个业务场景**: 客户打进来说"我的卡丢了" — 系统要一路护送他走完整个旅程: Bot 先接待 → 识别到挂失 → 转人工 → 排队等坐席 → 坐席接听 → 通话 → 挂断 → 话后小结 → 归档. 任何一个环节, 系统都得**准确知道"客户现在到哪一步了"**: 排队超时了要不要回 Bot? 坐席 30 秒没接要不要提醒? 通话中客户还能不能插话? 这就是状态机存在的意义 — **给每一次对话一个"当前进度", 让系统所有组件 (Bot、坐席端、超时器、审计) 对"现在是什么状态"有一致的认识**.

```mermaid
stateDiagram-v2
    [*] --> BOT_ACTIVE: 客户进入 Bot

    BOT_ACTIVE --> AG_QUEUED: 客户请求转人工
    BOT_ACTIVE --> ENDED: 客户说再见

    AG_QUEUED --> AG_ASSIGNED: 坐席接单
    AG_QUEUED --> ENDED: 客户放弃排队 (超时)
    AG_QUEUED --> BOT_ACTIVE: 客户撤销转人工

    AG_ASSIGNED --> AG_ACTIVE: 坐席首次响应
    AG_ASSIGNED --> ENDED: 坐席未响应 (超时)
    AG_ASSIGNED --> AG_QUEUED: 客户取消转接

    AG_ACTIVE --> AG_ON_HOLD: 坐席保持 (hold)
    AG_ON_HOLD --> AG_ACTIVE: 坐席恢复 (resume)
    AG_ON_HOLD --> ENDED: 保持超时 (60s)

    AG_ACTIVE --> AG_REVIEWING: 通话结束
    AG_REVIEWING --> ENDED: 话后小结完成 + PG 落库
    AG_REVIEWING --> AG_ACTIVE: 复核发现问题, 重新激活

    ENDED --> [*]
```

### 6.1.0 状态图解读 — "一次挂失通话的完整旅程"

顺着图从上往下走一遍 (对应上面客户的挂失场景):

1. **BOT_ACTIVE (Bot 接待)**: 客户进来先和 Bot 聊. 这是默认状态, 一切对话的起点.
2. **→ AG_QUEUED (排队中)**: Bot 识别到挂失 → 转人工. 客户进入排队, 系统开始 60 秒倒计时.
3. **→ AG_ASSIGNED (已分配)**: 坐席接了单, 系统通知客户"客服马上来". 如果 30 秒没人接 → 回排队或结束.
4. **→ AG_ACTIVE (通话中)**: 坐席开口了, 正式通话. 这是**双向**状态 — 坐席可以随时把客户保持 (AG_ON_HOLD) 或结束通话.
5. **→ AG_REVIEWING (话后小结)**: 通话结束, 坐席要在 2 分钟内填完小结 (记录问题、处理结果).
6. **→ ENDED (归档)**: 小结提交, 对话写入 PostgreSQL 审计留存 5-7 年.

**为什么状态不能乱跳**: 比如客户在排队 (AG_QUEUED) 时, 系统**不允许**直接跳到"通话中" (AG_ACTIVE) — 必须先经过"已分配" (AG_ASSIGNED). 这张图的箭头就是**合法转换白名单**, 代码里是 `VALID_TRANSITIONS` 表, 任何白名单外的转换直接抛 `InvalidTransitionError` — 防止并发情况下两个组件同时改状态导致"客户明明还在排队, 系统却以为在通话"的错乱.

**4 phase** (顶层):

| Phase | 含义 | 进入条件 |
|---|---|---|
| `BOT` | Bot 自助期 | 客户首次进入系统 |
| `AGENT` | 坐席通话期 | 客户转人工后 |
| `ENDED` | 通话结束 | 双方挂断 / 超时 |
| `legacy` | 旧版本兼容 | 旧数据迁移 |

**7 sub-state** (子状态):

| SubPhase | 含义 |
|---|---|
| `BOT_ACTIVE` | Bot 正常对话 |
| `AG_QUEUED` | 转人工排队中 |
| `AG_ASSIGNED` | 已分配坐席, 等待首次响应 |
| `AG_ACTIVE` | 坐席与客户对话中 |
| `AG_ON_HOLD` | 坐席主动保持 (hold) |
| `AG_REVIEWING` | 通话结束, 等待话后小结 |
| `ENDED` | 完全结束, PG 已落库 |

`VALID_TRANSITIONS` 白名单 (models.py) 严格控制哪些转换合法, 任何非法转换抛 `InvalidTransitionError (3005)`.

## 6.2 `SessionManager` 完整 API

`agent/lumio/services/common/session.py:92-280` `SessionManager` 集中所有会话操作:

```python
# session.py:92 (简化)
class SessionManager:
    def __init__(self, redis_client, dialogue_log_repo=None):
        self.redis = redis_client
        self.dialogue_log_repo = dialogue_log_repo
        self._cas_script = redis_client.register_script(_CAS_WRITE_SCRIPT)

    async def get_or_create(self, session_id: str, customer_id: str) -> SessionState:
        """获取或创建会话, UUID v7"""
        meta_key = session_meta_key(session_id)
        if not await self.redis.exists(meta_key):
            state = SessionState(
                session_id=session_id,
                customer_id=customer_id,
                phase=SessionPhase.BOT,
                sub_phase=SessionSubPhase.BOT_ACTIVE,
                version=0,  # CAS version
            )
            await self._create(state)
        return await self._load(session_id)

    async def add_turn(self, session_id: str, turn: DialogueTurn):
        """追加对话轮次, RPUSH + LTRIM 20"""
        history_key = session_history_key(session_id)
        await self.redis.rpush(history_key, turn.model_dump_json())
        await self.redis.ltrim(history_key, -20, -1)
        # TTL = max(配置, session_timeout×2+300) = 3900s (第五轮修复)
        # 旧默认 1800 与 AG_ACTIVE 超时相等: 空闲会话 meta 先过期 → get_session 返回
        # None → 超时轮询器吞错 → 会话永不走 ENDED → persist_dialogue 不触发 (审计缺口)
        await self.redis.expire(history_key, self._ttl)

    async def transition_phase(
        self, session_id: str, to_phase: SessionPhase, to_sub: SessionSubPhase, reason: str = ""
    ) -> SessionState:
        """CAS 原子转换"""
        # 1. 校验白名单
        if (to_phase, to_sub) not in VALID_TRANSITIONS.get(current_phase, {}):
            raise InvalidTransitionError(
                f"非法转换 {current_phase}/{current_sub} → {to_phase}/{to_sub}",
                code=3005,
            )
        # 2. CAS Lua 原子写
        new_version = await self._cas_script(
            keys=[session_meta_key(session_id)],
            args=[to_phase.value, to_sub.value, reason, ...],
        )
        if new_version == -1:
            # version 冲突, 重试 1 次
            raise StateConflictError(...)
        # 3. 指标 + 日志
        SESSION_TRANSITIONS.labels(
            from_phase=current_phase.value, from_sub=current_sub.value,
            to_phase=to_phase.value, to_sub=to_sub.value, reason=reason,
        ).inc()
        return new_state

    async def patch_state(
        self, session_id: str, patch: dict, mode: str = "merge"
    ) -> SessionState:
        """字段级合并, 3 种模式: 增量 / 单向门 / 全量覆写"""
        # _INCREMENTAL_FIELDS: intent_stack / entity_pool (去重合并)
        # _ONE_WAY_FIELDS: suppress_flag (false → true 单向)
        # 其他: 全量覆写
        ...
```

**关键设计**: 5 个核心 API: `get_or_create` / `add_turn` / `transition_phase` / `patch_state` / `persist_dialogue`. 任何会话操作必走这 5 个, 业务层零 Redis 接触.

## 6.3 CAS Lua 脚本: 原子控制

`session.py:67-95` 是核心 Lua 脚本:

```lua
-- session.py:67 (完整)
local current_version = tonumber(redis.call('HGET', KEYS[1], 'version'))
if current_version == nil then
    return -1  -- key 不存在
end
local expected_version = tonumber(ARGV[1])
if current_version ~= expected_version then
    return -1  -- version 冲突
end
-- 原子更新
redis.call('HSET', KEYS[1], 'phase', ARGV[2], 'sub_phase', ARGV[3], 'reason', ARGV[4])
redis.call('HINCRBY', KEYS[1], 'version', 1)
-- 合并 patch (JSON)
local patch_json = ARGV[5]
if patch_json and patch_json ~= '' then
    local patch = cjson.decode(patch_json)
    for k, v in pairs(patch) do
        if type(v) == 'table' and v.op == 'merge' then
            -- 增量合并 (去重)
            local existing = redis.call('HGET', KEYS[1], k)
            if existing then
                local existing_list = cjson.decode(existing)
                for _, item in ipairs(v.value) do
                    if not list_contains(existing_list, item) then
                        table.insert(existing_list, item)
                    end
                end
                redis.call('HSET', KEYS[1], k, cjson.encode(existing_list))
            else
                redis.call('HSET', KEYS[1], k, cjson.encode(v.value))
            end
        elseif type(v) == 'table' and v.op == 'one_way' then
            -- 单向门 (false → true)
            local existing = redis.call('HGET', KEYS[1], k)
            if not existing or existing == 'false' then
                redis.call('HSET', KEYS[1], k, tostring(v.value))
            end
        else
            -- 全量覆写
            redis.call('HSET', KEYS[1], k, cjson.encode(v))
        end
    end
end
return current_version + 1
```

**关键设计**:
- Lua 在 Redis 单线程内**原子执行**, 不会被其他命令打断
- version 不匹配立即返 -1, Python 侧重试
- 字段级合并: 3 种模式 (增量 / 单向门 / 全量), 全部在 Redis 端做, 减少网络往返
- `_INCREMENTAL_FIELDS = {"intent_stack", "entity_pool"}`: 数组去重合并
- `_ONE_WAY_FIELDS = {"suppress_flag"}`: false → true 单向, 不允许反向

**为什么不用 Redis 内置 `WATCH/MULTI`**:
- WATCH/MULTI 失败时**整批回滚**, 需要重试整批逻辑
- Lua 脚本**细粒度控制**, 字段级合并逻辑可嵌入
- Lua 性能更好 (一次 RTT)

## 6.4 字段级合并规则

`session.py:80-95` 定义 3 种合并模式:

```python
# session.py:80 (简化)
INCREMENTAL_FIELDS = {"intent_stack", "entity_pool", "recent_topics"}  # 增量去重
ONE_WAY_FIELDS = {"suppress_flag", "transferred_to_human"}             # 单向门
# 其他字段: 全量覆写
```

**场景示例**:

| 字段 | 模式 | 例子 |
|---|---|---|
| `intent_stack` | 增量 | `["complaint"]` + `["faq", "complaint"]` → `["complaint", "faq"]` (去重) |
| `suppress_flag` | 单向 | `false` + `true` → `true`; `true` + `false` → 仍是 `true` |
| `phase` | 全量 | `BOT` + `AGENT` → `AGENT` (覆盖) |
| `customer_id` | 全量 | 不变 (业务不变更) |

**为什么用 3 模式而非简单全量覆写**:
- **增量**: 意图栈需要累积而非覆盖
- **单向门**: `suppress_flag` 一旦设了 true, 不允许回退 (防止竞争条件下误关)
- **全量**: phase 等明确字段直接覆写最简单

## 6.5 ZSET 超时队列: 5s 轮询

`agent/lumio/services/common/session_timeout.py:44-180` 用 Redis ZSET 做分布式超时:

```python
# session_timeout.py:44 (简化)
TIMEOUT_ZSET_KEY = "lumio:session:timeouts"

class SessionTimeoutManager:
    """5 类超时统一管理"""

    async def start_guard(
        self, session_id: str, sub_phase: SessionSubPhase, timeout_type: TimeoutType, ttl: int
    ):
        """ZADD score=expire_ts"""
        score = int(time.time()) + ttl
        await self.redis.zadd(
            TIMEOUT_ZSET_KEY,
            {f"{session_id}:{timeout_type.value}": score},
        )

    async def cancel_guard(self, session_id: str, timeout_type: TimeoutType):
        """ZREM 取消"""
        await self.redis.zrem(TIMEOUT_ZSET_KEY, f"{session_id}:{timeout_type.value}")

    async def _poll_loop(self):
        """5s 一次轮询"""
        while True:
            await asyncio.sleep(5.0)
            now = int(time.time())
            # 1. 找出所有过期项
            expired = await self.redis.zrangebyscore(TIMEOUT_ZSET_KEY, 0, now)
            for member in expired:
                session_id, timeout_type = member.rsplit(":", 1)
                # 2. ZREM 原子竞争 (多实例只有一个能删成功)
                removed = await self.redis.zrem(TIMEOUT_ZSET_KEY, member)
                if removed == 0:
                    continue  # 其他实例已经处理
                # 3. 触发超时处理
                await self._handle_timeout(session_id, TimeoutType(timeout_type))
```

**超时守卫映射** (`session_timeout.py:238-248`):

| SubPhase | TTL | 触发动作 |
|---|---|---|
| `BOT_ACTIVE` | 180s | 客户 180s 无消息 → ENDED (reason=`bot:active_timeout`) |
| `AG_QUEUED` | 60s | 排队 60s 仍无坐席 → **回退 BOT** (降级而非结束) |
| `AG_ASSIGNED` | 无守卫 | 由外部 chat-svc 驱动振铃, 本地不设超时 |
| `AG_ACTIVE` | 1800s | 通话总时长 30 分钟 → ENDED |
| `AG_ON_HOLD` | 1800s | 保持超 30 分钟 → ENDED |
| `AG_REVIEWING` | 120s | 通话结束 2 分钟内必须生成话后小结 → ENDED |

**守卫生命周期** (关键设计):

- **创建即启动**: `create_session` 时即启动 `BOT_ACTIVE` 守卫 — 新会话也能被空闲超时回收, 不依赖 Redis TTL 兜底
- **随对话续期**: `add_turn` 每轮对话刷新 BOT 守卫 (`start_guard` 幂等 ZREM+ZADD) — 活跃会话不会被空闲超时误杀; 若只依赖 transition 启停, 出现过"转人工回退后的会话 120s 必被误杀、全新会话却永不超时"的行为不一致
- **`start_guard` 原子性**: 先 await ZREM 清旧守卫再 ZADD 新守卫 (顺序执行), 避免竞态删掉新守卫

**关键设计**: ZSET 而非 asyncio.Task. 原因:
- **多实例支持**: Bot 和 Assist 都能处理同一会话超时
- **重启恢复**: 服务重启后 ZSET 数据仍在 Redis, 自动恢复
- **原子竞争**: `ZRANGEBYSCORE` 查 + `ZREM` 删 是 2 步, 但 ZREM 只删一个成员, 多实例并发只有一个成功

## 6.6 PostgreSQL 异步落库

会话结束 (`ENDED` phase) 时, 异步落 `dialogue_log` 表:

```python
# session.py:380 (简化)
async def persist_dialogue(self, session_id: str):
    """会话结束, 异步落 PG dialogue_log 表"""
    # 1. 拉取完整历史
    history = await self.redis.lrange(session_history_key(session_id), 0, -1)
    meta = await self.redis.hgetall(session_meta_key(session_id))

    # 2. 转 DialogueLog ORM 模型
    log = DialogueLog(
        session_id=session_id,
        customer_id=meta["customer_id"],
        phase=meta["phase"],
        sub_phase=meta["sub_phase"],
        turns=[DialogueTurn.parse_raw(t) for t in history],
        started_at=datetime.fromisoformat(meta["created_at"]),
        ended_at=datetime.utcnow(),
        total_turns=len(history),
        compressed_json=compress(json.dumps([t.dict() for t in history])),
    )

    # 3. 异步落库 (fire-and-forget, 不阻塞)
    asyncio.create_task(self.dialogue_log_repo.insert(log))

    # 4. 清理 Redis (可选, 留 7 天备份)
    await self.redis.expire(session_meta_key(session_id), 7 * 86400)
    await self.redis.expire(session_history_key(session_id), 7 * 86400)
```

**关键设计**:
- **5-7 年留存**: PG 表 `dialogue_log` 由 DBA 配置保留期, 金融合规要求
- **压缩存储**: `compressed_json` 字段 (BYTEA) 压缩对话原文, 减少存储
- **fire-and-forget**: `asyncio.create_task` 不阻塞 `transition_phase` 调用
- **Redis 留 7 天**: 临时备份, 7 天后过期

## 6.7 Redis key 集中化

Redis key 命名集中在 `session.py` 统一管理:

```python
# session.py:24-50 (简化)
def session_meta_key(session_id: str) -> str:
    return f"lumio:session:{session_id}:meta"

def session_history_key(session_id: str) -> str:
    return f"lumio:session:{session_id}:history"

def session_meta_scan_pattern() -> str:
    return "lumio:session:*:meta"

def session_timeout_zset_key() -> str:
    return "lumio:session:timeouts"

# SessionManager 内部使用这些 helpers, 不再硬编码
class SessionManager:
    def _meta_key(self, session_id: str) -> str:
        return session_meta_key(session_id)  # 委托
    def _history_key(self, session_id: str) -> str:
        return session_history_key(session_id)  # 委托
```

**集中管理收益**:
- **未来 prefix 改动**: 单点修改 `session.py:24-50`, 全局生效
- **测试更简单**: 测试可以 mock helper 函数
- **审计清晰**: `session_meta_scan_pattern()` 给 SCAN 用, 避免散落

## 6.8 状态模型: 3×7 矩阵

会话状态机采用 **3 phase (顶层) × 6 sub-state (子)** 的矩阵模型:

```
PHASE:   BOT → AGENT → ENDED
SUB:     IDLE / ACTIVE / WAITING_HUMAN / QUEUING / ASSIGNED / ON_HOLD / REVIEWING ...
```

设计动机:
- 扁平状态互相转换规则复杂 (N×N 组合)
- `WAITING_HUMAN` 需要表达"排队中 vs 已分配"区别
- 终结态统一归 `ENDED`, 用 `reason` 字段区分 (abandoned / transferred / normal)
- 状态组合由 VALID_TRANSITIONS 白名单严格约束 (合法转换共 16 条), 非法转换直接拒绝

兼容策略:
- `legacy` phase 兼容旧数据读取

## 6.9 会话复活与收尾 (多轮对话边界)

### 6.9.1 ENDED 会话复活

客户超时/主动结束后再次发消息 → `router.py:_run_agent` 检测 `current_phase == ended`, 调
`transition_phase(BOT, BOT_ACTIVE, reason="customer_returned")` 复活会话:

```python
# router.py:607-619 (简化)
if state.current_phase.value == "ended":
    await session_manager.transition_phase(
        session_id, SessionPhase.BOT,
        new_sub_phase=SessionSubPhase.BOT_ACTIVE,
        reason="customer_returned",
    )
```

**设计要点**:
- 同一 session_id、同一 Redis key — 历史 / conversation_summary / 实体池 / 意图栈全部保留
- 守卫由 transition_phase 重新启动 (BOT_ACTIVE 180s 空闲超时生效)
- 若 meta 已过期 (Redis TTL 到) → `get_or_create` 新建会话, 画像经 customer_memory 90 天窗口重新学习

### 6.9.2 告别即收尾

客户说"再见/拜拜/谢谢" → `_is_farewell` 快速路径, 同时**真正结束会话**:

- `transition_phase(ENDED, reason="customer_farewell")` — 触发 PG 落库审计
- `pending_action` 一并清除 — 避免复活后第一句普通消息被当成确认/取消误判
- 回复模板话术, 不调 LLM

### 6.9.3 AG_ASSIGNED 死状态

`agent:assigned` (振铃) 在 lumio 本地**无触发路径**, 由外部 chat-svc 回调驱动:

- `VALID_TRANSITIONS` 保留表项 (兼容外部回调 `queued → assigned → active`)
- **本地不设超时守卫** (`_get_timeout` 无 AG_ASSIGNED 映射) — 避免本地 30s 误杀外部驱动的振铃会话

### 6.9.4 转人工排队期间的消息真实记录

会话进入 AGENT 阶段后客户发消息 → 返回"已为您记录"话术, **同时真实写入 Redis 历史**
(`add_turn`), 供坐席摘要 / 转回 Bot 时保留上下文 — 话术与事实一致.

## 6.10 监控指标

会话状态机发射 5 个核心指标 — 回答"会话流转健不健康、有没有卡死、有没有超时"：

| 指标 | 类型 | 含义 | Labels | 位置 |
|---|---|---|---|---|
| `session_transitions_total` | Counter | **状态转换次数** (看客户从哪阶段流向哪阶段) | from_phase, from_sub, to_phase, to_sub, reason | session.py:403 |
| `session_timeouts_total` | Counter | **超时触发次数** (哪个子阶段超时最多) | sub_phase, reason | session_timeout.py:155 |
| `session_phase_duration_seconds` | Histogram | **各子阶段停留时长** (排队等多久/通话多久) | sub_phase | session.py:412 (5s-3600s buckets) |
| `state_conflict_retries_total` | Counter | **CAS 并发冲突重试次数** (高 = 并发写太激烈) | - | session.py:285 (CAS 冲突重试) |
| `dialogue_log_persist_total` | Counter | **对话落库成败** (error 高 = 审计留痕有缺口) | status (success/error) | session.py:395 |

**指标 0 冲突**: 全部指标名 + label 在 Grafana dashboard 有对应 panel.

## 6.11 测试覆盖

`agent/tests/` 中会话相关:

- `test_session.py` (25 用例, **TOP 4**): 会话生命周期 + 转换
- `test_state_models.py` (28 用例, **TOP 3**): 状态机 ORM 模型 + VALID_TRANSITIONS
- `test_session_timeout.py` (15 用例): ZSET 超时 + 5 类
- `test_session_lifecycle_e2e.py` (e2e, CI 排除): 完整流程
- `test_state_conflict.py` (10 用例): CAS 冲突重试

**关键测试** (test_session.py:570-578):

```python
# 验证 CAS 增量合并
async def test_intent_stack_incremental_merge(redis):
    mgr = SessionManager(redis)
    await mgr.get_or_create("s1", "c1")
    # 添加 3 个意图, 模拟重复
    await mgr.patch_state("s1", {"intent_stack": {"op": "merge", "value": ["complaint"]}})
    await mgr.patch_state("s1", {"intent_stack": {"op": "merge", "value": ["faq"]}})
    await mgr.patch_state("s1", {"intent_stack": {"op": "merge", "value": ["complaint"]}})  # 重复
    # 验证去重后只有 2 个
    state = await mgr.get_or_create("s1", "c1")
    assert len(state.intent_stack) == 2
    assert "complaint" in state.intent_stack
    assert "faq" in state.intent_stack
```

## 6.12 本章小结

会话状态机是 Lumio 处理客户对话的"中枢神经":

- **3 phase × 7 sub-state**: 21 组合, 11 合法转换, 白名单控制
- **CAS Lua 原子控制**: 单 RTT, 字段级合并, 失败重试
- **3 种字段合并模式**: 增量去重 / 单向门 / 全量覆写
- **ZSET 分布式超时**: 替代 asyncio.Task, 多实例支持, 5s 轮询 + ZREM 原子竞争
- **5 类超时**: BOT_IDLE / QUEUE / RINGING / SESSION / REVIEW, 各 TTL 不同
- **PG 异步落库**: 5-7 年合规留存, fire-and-forget 不阻塞
- **key 集中化**: 未来 prefix 改动单点

> **下一章预告**: [第 7 章 MCP 工具集成](07-mcp-tool-integration.md) 深入 22 个 Java 工具 + Higress 网关 + Python 端零回归.

---

> **延伸阅读**:
> - [第 3 章 Bot 自助问答](03-bot-self-service.md) — pending_action 跨轮持久化
> - [第 12 章 数据层](chapters/12-data-layer.md) — Redis ZSET + CAS Lua 详解
> - [附录 A 术语表](appendix/A-glossary.md#a3-会话状态机) — 状态机术语速查
> - [第 16 章 客户记忆与知识图谱](chapters/16-customer-memory-and-kg.md) — 跨会话画像学习 (vip_level / card_types / risk_tolerance CAS patch 写入 SessionState)
