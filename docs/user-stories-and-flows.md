# SmartCS 用户故事与交互流程

> 版本: v2.3 | 日期: 2026-05-22 | 状态: 待评审

---

## 一、角色定义

| 角色 | 代号 | 描述 |
|------|------|------|
| 客户 | CU | 信用卡持卡人，通过 Web/App/微信/电话 咨询信用卡相关问题 |
| 坐席 | AG | 银行客服代表，在 Agent Workbench 接听客户来电 |
| 运营 | OP | 知识库管理员，上传/维护知识文档和质检规则 |
| 系统 | SYS | SmartCS 平台自动行为，无需人工介入 |

---

## 二、会话状态机

### 2.1 三阶段 × 七子阶段模型

会话是贯穿整个服务的核心实体，采用 **3 阶段 (Phase) × 7 子阶段 (SubPhase)** 模型：

```
                          ┌─────────────────────────────────────────────────┐
                          │               AGENT 阶段                       │
                          │                                                 │
  ┌──────────┐     ┌──────┴──────┐    ┌─────────────┐    ┌───────────┐    │
  │   BOT    │────▶│  AG_QUEUED  │───▶│ AG_ASSIGNED │───▶│ AG_ACTIVE │◀──┐│
  │ bot:active│     │  agent:queued│    │agent:assigned│    │agent:active│   ││
  └──────────┘     └──────┬──────┘    └─────────────┘    └─────┬─────┘   ││
       │               │                                    │         ││
       │               │ (排队超时)                          │(保持)    ││
       │               ▼                                    ▼         ││
       │          ┌──────────┐                        ┌───────────┐  ││
       └─────────▶│  回退BOT  │                        │ AG_ON_HOLD │──┘│
    (L1/L2/L3     │ bot:active│                        │agent:on_hold│   │
     未命中)      └──────────┘                        └───────────┘   │
                                                       (超时→ENDED)    │
                                                                       │
                          ┌──────────────┐                            │
                          │ AG_REVIEWING │─────────────────────────────┘
                          │agent:reviewing│     (提交小结→ENDED)
                          └──────────────┘
                               (超时→ENDED)
```

| 阶段 | 子阶段 | 触发条件 | 系统行为 |
|------|--------|---------|---------|
| **BOT** | `bot:active` | 客户发起会话 | LangGraph Agent 处理消息，意图分类+路由分发 (knowledge→RAG / business→API / fallback→模板) |
| **AGENT** | `agent:queued` | L1/L2/L3 转人工触发 | 调用 star-connection 创建人工会话，进入排队 |
| **AGENT** | `agent:assigned` | star-connection 分配坐席 | 坐席振铃，超时守卫启动 (默认 30s) |
| **AGENT** | `agent:active` | 坐席接听 | WS 消息激活会话，OE 编排启动辅助推送，超时守卫 (默认 1800s) |
| **AGENT** | `agent:on_hold` | 坐席点击保持 | 启动静音检测 (60s)，超时守卫继续计时 |
| **AGENT** | `agent:reviewing` | 坐席点击生成小结 | LLM 生成话后小结，超时守卫 (默认 120s) |
| **ENDED** | — | 坐席提交小结 / 超时 / 客户断开 | WS 会话数据清理，反馈归档，审计日志写入 |

### 2.2 状态转换白名单

```
(bot, bot:active)      → {agent:queued, ended}
(agent, agent:queued)  → {agent:assigned, bot:active, ended}
(agent, agent:assigned) → {agent:active, agent:queued, ended}
(agent, agent:active)  → {agent:on_hold, agent:assigned, agent:reviewing, bot:active, ended}
(agent, agent:on_hold) → {agent:active, ended}
(agent, agent:reviewing) → {ended}
```

非法转换将抛出 `ValueError`，由 `validate_transition()` 校验。

### 2.3 超时守卫

每个子阶段启动时自动创建 `asyncio.Task` 超时守卫，超时后自动执行状态转换：

| 子阶段 | 超时配置 | 超时行为 |
|--------|---------|---------|
| `bot:active` | `bot_idle_timeout` (120s) | → ENDED |
| `agent:queued` | `queue_timeout` (60s) | → BOT (回退自助) |
| `agent:assigned` | `ringing_timeout` (30s) | → ENDED |
| `agent:active` | `session_timeout` (1800s) | → ENDED |
| `agent:on_hold` | `session_timeout` (1800s) | → ENDED |
| `agent:reviewing` | `review_timeout` (120s) | → ENDED |

超时触发时通过 WebSocket 向坐席 UI 推送 `session_timeout` 事件，同时记录 Prometheus `SESSION_TIMEOUTS` 指标。

---

## 三、转人工三级触发机制 (L1/L2/L3)

Bot 侧判断"何时把客户转给人工坐席"的三层规则，优先级 **L1 > L2 > L3**，命中高级别直接触发：

### L1 — 关键词触发

**即时响应，无延迟。** 扫描用户输入是否命中转人工关键词或敏感词。

| 检查项 | 词库来源 | 例子 |
|--------|---------|------|
| 转人工关键词 | 硬编码默认 + `config/transfer_keywords.txt` | "人工"、"转人工"、"真人"、"找经理"、"投诉" |
| 敏感词 | `config/sensitive_words.txt` | 业务敏感词 |

触发条件：用户输入包含任意关键词。

### L2 — 语义触发

**依赖意图分类结果，1 轮内判断。** 基于意图和情感分析结果。

| 触发条件 | 说明 |
|---------|------|
| 意图 = `transfer_agent` | 用户明确表达转人工意愿 |
| 意图 = `complaint` | 投诉意图，需要人工介入 |
| 情感 ∈ {negative, angry} 且置信度 > 0.8 | 客户情绪恶化，Bot 无法安抚 |

### L3 — 累计触发

**跨轮次累积，反映持续不满意。** 依赖会话历史状态。

| 触发条件 | 说明 |
|---------|------|
| 连续 N 轮低置信度 (默认 3) | `low_confidence_streak ≥ threshold`，Bot 连续无法给出高置信回答 |
| 最近 5 轮中 3 轮兜底回复 | `confidence_history[-5:]` 中低置信度占比过高 |

### 调用链

```
classify_intent → supervisor → {knowledge/business/fallback}_agent → transfer_check
                                                                           │
                                                              TransferChecker.check()
                                                              ├ L1 _check_l1() ──→ 命中即返回
                                                              ├ L2 _check_l2() ──→ 命中即返回
                                                              └ L3 _check_l3() ──→ 命中即返回
                                                                           │
                                                              should_transfer?
                                                              ├ Yes → transfer_node → SessionPhase.AGENT (AG_QUEUED)
                                                              └ No  → respond_node → 返回 Bot 回复
```

---

## 四、核心交互流程

### 流程 1：客户自助咨询 (Bot) — 生产级架构

**消息通道**: Redis Streams 持久化 + Consumer Group + PEL 兜底
**结果通知**: Redis Pub/Sub 即时唤醒 + Redis response key 作为结果载体
**消费模式**: XREADGROUP 快进快出 + `create_task` 异步并行，per-session Queue + Worker 保序

```
  CU (客户端)               Bot :8000                Redis                     Worker (协程)           SessionMgr
  ──────────               ────────                 ─────                     ────────────           ──────────
      │                         │                      │                            │                     │
      │ ═══ Step 1: 发送消息 ═══════════════════════════════════════════════════════│                     │
      │                         │                      │                            │                     │
      │──POST /chat/send──────▶│                      │                            │                     │
      │                         │──XADD────────────────▶                            │                     │
      │                         │  chat:stream *       │                            │                     │
      │                         │  {session_id,message}│                            │                     │
      │◀── 202 Accepted ───────│                      │                            │                     │
      │                         │                      │                            │                     │
      │ ═══ Step 2: 长轮询等待 (Pub/Sub 唤醒) ═══════════════════════════════════════│                     │
      │                         │                      │                            │                     │
      │──GET /chat/poll────────▶│                      │                            │                     │
      │  ?session_id=xxx&t=25   │                      │                            │                     │
      │                         │──GET response:xxx─────────────────────────────▶   │                     │
      │                         │◀── nil (未就绪) ───────────────────────────────│   │                     │
      │                         │──SUBSCRIBE notify:xxx─────────────────────────▶   │                     │
      │                         │                      │                            │                     │
      │                         │    [阻塞等待 Pub/Sub 消息]                         │                     │
      │                         │                      │                            │                     │
      │ ═══ Step 3: 消费与处理 ══════════════════════════════════════════════════════│                     │
      │                         │                      │                            │                     │
      │                         │                      │◀──XREADGROUP───────────────│                     │
      │                         │                      │   bot-group main >         │                     │
      │                         │                      │   COUNT 10                │                     │
      │                         │                      │──消息──────────────▶       │                     │
      │                         │                      │                            │                     │
      │                         │                      │   create_task(_process())  │                     │
      │                         │                      │   ← 立即返回，不阻塞消费循环 │                     │
      │                         │                      │                            │                     │
      │                         │                      │              ┌─────────────┴──────┐              │
      │                         │                      │              │ _process(msg)      │              │
      │                         │                      │              │                    │              │
      │                         │                      │              │ 1. 幂等检查         │              │
      │                         │                      │              │    EXISTS resp:{sid}│              │
      │                         │                      │              │    → 是: 直接 XACK │              │
      │                         │                      │              │                    │              │
      │                         │                      │              │ 2. per-session Queue│              │
      │                         │                      │              │    路由到独享Worker │              │
      │                         │                      │              │                    │              │
      │                         │                      │              │ 3. get_or_create() │──────────────▶│
      │                         │                      │              │                    │  返回 session  │
      │                         │                      │              │                    │◀──────────────│
      │                         │                      │              │                    │              │
      │                         │                      │              │ 4. Agent.run()     │              │
      │                         │                      │              │    意图分类        │              │
      │                         │                      │              │    路由分发        │              │
      │                         │                      │              │    分支处理        │              │
      │                         │                      │              │    转人工检查       │              │
      │                         │                      │              │    ~2-5s           │              │
      │                         │                      │              │                    │              │
      │                         │                      │              │ 5. add_turn()      │──────────────▶│
      │                         │                      │              │                    │  RPUSH history │
      │                         │                      │              │                    │◀──────────────│
      │                         │                      │              │                    │              │
      │                         │                      │              │ 6. SETEX response  │              │
      │                         │                      │◀─────────────│    key TTL 120s    │              │
      │                         │                      │              │                    │              │
      │                         │                      │              │ 7. PUBLISH notify  │              │
      │                         │                      │◀─────────────│    {sid} "ready"   │              │
      │                         │                      │              │                    │              │
      │                         │                      │              │ 8. XACK            │              │
      │                         │                      │◀─────────────│    从 PEL 移除      │              │
      │                         │                      │              └────────────────────┘              │
      │                         │                      │                            │                     │
      │ ═══ Step 4: 拿到结果 ═════════════════════════════════════════════════════════│                     │
      │                         │                      │                            │                     │
      │                         │◀── Pub/Sub 通知 ─────│                            │                     │
      │                         │   notify:xxx "ready" │                            │                     │
      │                         │──GET response:xxx─────────────────────────────▶   │                     │
      │                         │◀── 命中! ───────────────────────────────────────│   │                     │
      │                         │──DEL response:xxx──────────────────────────────▶   │                     │
      │                         │                      │                            │                     │
      │◀── 200 PollResponse ───│                      │                            │                     │
      │  {reply, intent,       │                      │                            │                     │
      │   confidence, source}  │                      │                            │                     │
      │                         │                      │                            │                     │
```

**关键设计**:

| 机制 | 实现 | 作用 |
|------|------|------|
| 消息持久化 | `XADD chat:stream *` | 消息写入 Stream 即持久，不随消费删除 |
| 消费确认 | `XREADGROUP` + 处理完 `XACK` | at-least-once 保证，Worker 崩溃消息留在 PEL |
| 队头阻塞消除 | 消费循环 `create_task` 立即返回 | 不等待 Agent 完成，下一条消息即时消费 |
| 同会话顺序 | per-session Queue + 独享 Worker | 每会话一个消费协程，天然串行，无锁，可跳过过期消息 |
| 无轮询延迟 | `PUBLISH notify:{sid}` 唤醒 | Worker 完成即刻通知，不再每 0.5s 盲等 |
| 结果载体 | `SETEX response:{sid} 120` | 结果可被重启后的 Bot 读取，Pub/Sub 只传信号不传数据 |
| 幂等处理 | 处理前 `EXISTS response:{sid}` | 重试时不重复调用 Agent |
| 宕机兜底 | `XAUTOCLAIM` 协程 | 60s 超时未 XACK 的消息自动认领重试 |
| 分支处理耗时 | knowledge: 2~5s, business: 500ms~1.5s, fallback: <10ms | RAG 检索+LLM 生成 / 核心API / 模板 |

**过载保护 — 快速兜底话术（银行不能拒客）**:

Semaphore 满荷时走快速通道：regex 快速意图匹配 (< 5ms) → 返回对应固定话术 → `source=fast_reply`。不等待 slot 释放，不倒逼客户端重试。

| 意图 | 快速兜底话术 |
|------|-------------|
| `lost_card` 挂失 | "挂失为紧急业务，正在为您优先处理，请稍候。如超过 10 秒未回复，请直接输入'转人工'。" |
| `complaint` 投诉 | "您的投诉已记录，正在转接人工处理。" |
| `bill_query` 账单 | "当前咨询量较大，账单查询结果稍后返回，也可输入'转人工'联系客服。" |
| `limit_query` 提额 | "您的问题正在处理中，预计 30 秒内回复。" |
| `default` 默认 | "当前咨询量较大，请稍候或输入'转人工'。" |

**三条处理路径**:

| 路径 | 触发条件 | 延迟 | 回复方式 | 适用场景 |
|------|---------|------|---------|---------|
| 标准 Agent | Semaphore 有空槽 | 2-5s | LLM 生成 | 正常流量 |
| 快速兜底 | Semaphore 满 | <50ms | 固定话术 | 峰值流量 |
| 直接转人工 | 挂失/投诉等紧急意图 | <100ms | 转接提示 | 任何时候 |

**轮询状态（客户端感知排队）**:

GET /chat/poll 返回不同状态，让客户感知节奏而非黑盒等待：

```json
{"status": "queued",    "position": 3, "est_wait": "约15秒"}   // 排队中
{"status": "processing"}                                       // 正在处理
{"status": "done",      "reply": "..."}                        // 已完成
{"status": "timeout",   "suggestion": "请稍后重试或输入'转人工'"}  // 超时
```

### 流程 2：客户转人工 (Bot → AGENT)

转人工三步：**发起**（Bot 调用 star-conn）→ **等待+接听**（star-conn 内部路由）→ **激活**（AgentUI 已有 Assist WS，发消息即激活）。

> AgentUI 与 Assist 之间是 **1 条 WS（坐席登录时建立，持久的）**，非按会话建连。会话上下文通过消息中 `session_id` 字段区分。

```
  CU              Bot:8000                     star-conn        AgentUI       Assist:8001
  ──              ────────                     ─────────        ───────       ──────────
   │                   │                            │               │               │
   │ ═══ Step 1: 发起转人工 ══════════════════════════════════════════════════════│
   │                   │                            │               │               │
   │──"我要转人工"────▶│                            │               │               │
   │                   │                            │               │               │
   │                   │─ TransferChecker: L1 命中   │               │               │
   │                   │─ transition(BOT→AG_QUEUED)  │               │               │
   │                   │─ LLM 生成转接摘要           │               │               │
   │                   │                            │               │               │
   │                   │──create_session({sid,       │               │               │
   │                   │    reason, summary,         │               │               │
   │                   │    history[20], intent})──▶│               │               │
   │                   │                            │               │               │
   │                   │◀── {star_sid, poll_url} ──│               │               │
   │                   │                            │               │               │
   │◀──"正在为您转接" + poll_url ─────│                            │               │
   │                   │                            │               │               │
   │ ═══ Step 2: 等待+接听 (star-conn 内部路由, SmartCS 不感知) ═══════════════│
   │                   │                            │               │               │
   │                   │                   ┌────────┴────────┐      │               │
   │                   │                   │ 坐席负载均衡      │      │               │
   │                   │                   │ 可用→振铃(30s)   │      │               │
   │                   │                   │ 忙  →排队(60s)   │      │               │
   │                   │                   │ 坐席接受→继续    │      │               │
   │                   │                   │ 超时→回退/ENDED  │      │               │
   │                   │                   └────────┬────────┘      │               │
   │                   │                            │               │               │
   │   [客户通过 poll_url 感知排队进度]               │               │               │
   │                   │                            │               │               │
   │                   │                      [坐席接受]             │               │
   │                   │                            │               │               │
   │                   │                            │──会话面板打开──▶│               │
   │                   │                            │               │               │
   │                   │                            │               │──WS {type:     │
   │                   │                            │               │  session_     │
   │                   │                            │               │  activated,   │
   │                   │                            │               │  session_id}  │
   │                   │                            │               │              │
   │                   │                            │               │──transition──▶│
   │                   │                            │               │  AG_ACTIVE    │
   │                   │                            │               │              │
   │                   │                            │               │◀──WS assist_ │
   │                   │                            │               │  ready +画像 │
   │                   │                            │               │  +摘要+Bot历史│
   │                   │                            │               │              │
   │ ═══ Step 3: AGENT 阶段消息流 ═══════════════════════════════════════════════│
   │                   │                            │               │              │
   │──客户消息─────────│──────────────────────────▶│               │              │
   │                   │                            │──路由到坐席──▶│              │
   │                   │                            │──POST /notify──────────────▶│
   │                   │                            │  (202,<10ms) │  异步编排    │
   │                   │                            │               │  WS 推送    │
   │                   │                            │               │              │
```

**关键设计**:

| 机制 | 实现 | 作用 |
|------|------|------|
| 转接触发 | TransferChecker L1→L2→L3 | L1 瞬间命中，L2/L3 兜底 |
| 转接摘要 | Bot LLM 生成 → session meta | 坐席接听即刻看到上下文 |
| 排队感知 | poll_url 长轮询，每 15s 推送排队位置 | 客户感知进度 |
| 超时保护 | 排队 60s→回退BOT / 振铃 30s→ENDED | 避免无限等待 |
| 会话激活 | AgentUI → Assist WS 消息 `session_activated` | 无需 star-conn 回调，WS 消息即激活 |
| 消息流 | star-conn 路由客户消息到坐席，同时 POST /notify 给 Assist | 辅助与对话解耦 |
| WS 生命周期 | 坐席登录建连，上班持久，一个坐席一条 | 不与单个会话绑定 |
| 容错 | create_session 超时/失败 → "人工客服暂不可用" | 不阻塞 Bot |

### 流程 3：坐席辅助推送 (AGENT 阶段, 每条客户消息触发)

客户消息到达 → star-conn 双路分发（路由到坐席 UI + 通知 Assist）→ Assist 异步编排 → 通过已有的持久 WS 推给坐席。

> AgentUI 与 Assist 之间是流程 2 中已建立的持久 WS（按坐席），不在此流程中新建。

```
  CU              star-conn          Assist:8001 (内部)                    AgentUI
  ──              ─────────          ─────────────────                    ───────
   │                   │                      │                              │
   │──"我想提额"──────▶│                      │                              │
   │                   │                      │                              │
   │                   │──路由消息─────────────────────────────────────────▶│
   │                   │  (Netty)             │          消息显示             │
   │                   │                      │                              │
   │                   │──POST /notify──────▶│                              │
   │                   │  {sid, message}     │                              │
   │                   │◀──202 (<10ms)──────│                              │
   │                   │                      │                              │
   │                   │                      │  全局通知队列                 │
   │                   │                      │ ┌─ asyncio.Queue ──────────┐ │
   │                   │                      │ │ 分发协程:                │ │
   │                   │                      │ │  按 sid 路由到对应       │ │
   │                   │                      │ │  per-session Queue       │ │
   │                   │                      │ └──────────┬──────────────┘ │
   │                   │                      │            │                │
   │                   │                      │ ┌─ per-session Worker ────┐ │
   │                   │                      │ │ (每会话一个协程, 无锁)    │ │
   │                   │                      │ │                         │ │
   │                   │                      │ │ 1. 跳过过期消息(>8s)     │ │
   │                   │                      │ │ 2. 幂等检查              │ │
   │                   │                      │ │ 3. SessionMgr.get_session│ │
   │                   │                      │ │    加载上下文 (Redis)    │ │
   │                   │                      │ │ 4. 意图分类 (Rule+LLM)  │ │
   │                   │                      │ │ 5. OE 编排              │ │
   │                   │                      │ │    D1/D2/D3→E1∥E3→仲裁  │ │
   │                   │                      │ │    ~2-5s                │ │
   │                   │                      │ │ 6. WS 推送 (持久连接)   │ │
   │                   │                      │ └──────────┬──────────────┘ │
   │                   │                      │            │                │
   │                   │                      │──WS {type:"assist_push",───▶│
   │                   │                      │  sid, payload:{scripts,    │
   │                   │                      │  knowledge, alerts,       │
   │                   │                      │  recommendations}}        │
   │                   │                      │                              │
   │                   │                      │         ┌─双向:坐席回复─┐    │
   │                   │                      │◀──WS {type:"agent_msg"}│────│
   │                   │                      │     → 合规检测         │    │
   │                   │                      │     → 隐式反馈推断     │    │
   │                   │                      │         └──────────────┘    │
```

**OE 编排内部 (消费协程第5步展开)**:

```
                     notify 进入消费协程
                              │
              ┌───────────────┴───────────────┐
              │         D 评估阶段              │
              │                               │
              │  D1 服务: 置信度>阈值, 冷却2轮  │
              │  D2 营销: 情绪+意图, 冷却5轮    │
              │       (D1激活时 suppress 2轮)  │
              │  D3 风控: 始终激活, 无冷却      │
              └───────────────┬───────────────┘
                              │
              ┌───────────────┴───────────────┐
              │         E 执行阶段              │
              │                               │
              │  E1 AI 服务 (SLA 3s)            │
              │    LangGraph DAG → 话术+知识    │
              │  E3 风控 (SLA 100ms)           │
              │    AlertEngine → BLOCK/WARN/PASS│
              │  E2 营销 (延后 500ms)           │
              │    ProductCatalog → 产品推荐    │
              └───────────────┬───────────────┘
                              │
              ┌───────────────┴───────────────┐
              │       全局仲裁器                │
              │                               │
              │  优先级: BLOCK > WARN > PASS    │
              │  BLOCK: 仅风险卡片, 营销隐藏    │
              │  WARN:  服务+风险徽章, 营销缩小 │
              │  PASS:  服务+营销标准卡片       │
              │  + PII 脱敏 (递归处理)          │
              │  + 合规短语过滤                │
              └───────────────────────────────┘
```

**关键设计**:

| 机制 | 实现 | 作用 |
|------|------|------|
| 异步解耦 | POST /notify → 202 → asyncio.Queue | star-conn 不阻塞，<10ms 返回 |
| 顺序保证 | per-session Queue + 独享 Worker | 每会话一个协程，无锁无竞态，可跳过过期消息 |
| 幂等处理 | 处理前检查 response key | 防重复消费 |
| 上下文加载 | SessionManager.get_session() | 一次 Redis GET 拿到完整状态 |
| OE 编排 | D评估→策略矩阵→E并行→仲裁融合 | 服务+风控+营销三路协同 |
| 降级 | Temporal 不可用 → 同步编排器 | 失 OE 策略矩阵，仍有辅助推送 |
| WS 推送 | 复用坐席持久 WS，带 session_id | 推送直达，不新建连接 |
| 隐式反馈 | 监听坐席 WS 回复，推断采纳/修改 | 无感收集训练信号 |
| 合规检测 | 坐席消息实时检测 → 违规告警 WS 推送 | 双向（客户+坐席） |
| 节流 | throttle_window 内同会话只推 1 次 | 防刷屏，critical 告警绕过 |

### 流程 4：知识库入库与检索

#### 4.1 文档入库

运营上传文档 → MinIO 落地 → Postgres 记录 → 解析/清洗/分块 → 向量嵌入 → 双写 ES + Milvus。

```
  OP (运营)         Bot:8000           MinIO            Postgres         嵌入服务          ES/Milvus
  ────────         ────────           ─────            ────────         ──────          ────────
   │                   │                 │                  │               │                │
   │──POST /kb/docs───▶│                 │                  │               │                │
   │  (multipart)      │                 │                  │               │                │
   │                   │──put_object──▶  │                  │               │                │
   │                   │◀── OK ────────│                  │               │                │
   │                   │                 │                  │               │                │
   │                   │──INSERT KbDocument (status=PENDING)──────────▶    │                │
   │                   │                 │                  │               │                │
   │◀── {doc_id, status} ──────────────│                  │               │                │
   │                   │                 │                  │               │                │
   │                   │ ── 入库管线 (同步) ──────────────────────────────│                │
   │                   │                 │                  │               │                │
   │                   │  1. 解析 (pymupdf/python-docx)     │               │                │
   │                   │     PDF/DOCX/HTML/MD/XLSX         │               │                │
   │                   │                 │                  │               │                │
   │                   │  2. 清洗: 去页眉/控制符/去重      │               │                │
   │                   │     FAQ: H2 问答对提取             │               │                │
   │                   │     层级文档: Parent-Child 分块   │               │                │
   │                   │     表格保护: header 复制到子块    │               │                │
   │                   │                 │                  │               │                │
   │                   │  3. 嵌入 (batch=128)              │               │                │
   │                   │──────────────────────────────────▶│               │                │
   │                   │◀── vectors ──────────────────────│               │                │
   │                   │                 │                  │               │                │
   │                   │  4. 双写索引                       │               │                │
   │                   │     ES: BM25 索引                 │               │────────────▶   │
   │                   │     Milvus: 向量索引               │               │────────────▶   │
   │                   │                 │                  │               │                │
   │                   │  5. UPDATE KbDocument (status=COMPLETED) ──────▶   │                │
   │                   │                 │                  │               │                │
```

**入库管线说明**:

| 步骤 | 实现 | 说明 |
|------|------|------|
| 解析 | pymupdf / python-docx / openpyxl | 6 种格式: PDF, DOCX, HTML, MD, TXT, XLSX |
| 清洗 | 正则 + 去重 | 去页眉页脚、控制字符、SHA-256 去重 |
| 分块 | 结构感知: FAQ对 / 层级 / 表格 | FAQ: H2+答案对; 层级: Parent-Child; 表格: header 复制 |
| 嵌入 | Ollama / TEI (BGE-M3) | batch=128, 重试 3 次 |
| 索引 | ES BM25 + Milvus 向量 | 两条独立写路径, 互不阻塞 |

#### 4.2 混合检索

查询到达 → 双路并行检索 → RRF 融合 → 可选 Reranker 精排 → 结果截断返回。

```
  Bot:8000            ES                  Milvus            Embedding          Reranker
  ────────            ──                  ──────            ─────────          ────────
   │                   │                     │                  │                  │
   │──查询文本─────────│                     │                  │                  │
   │                   │                     │                  │                  │
   │  ┌────────────────┼─────────────────────┼──────────────────┼──────────────────┐
   │  │              双路并行检索 (互不依赖)   │                  │                  │
   │  │                │                     │                  │                  │
   │  │──BM25 search──▶│                     │                  │                  │
   │  │  top_k × 3     │                     │                  │                  │
   │  │                │                     │                  │                  │
   │  │                │────查询文本嵌入─────────────────────▶│                  │
   │  │                │                     │                  │                  │
   │  │                │                     │◀──vector search──│                  │
   │  │                │                     │  top_k × 3      │                  │
   │  └────────────────┼─────────────────────┼──────────────────┼──────────────────┘
   │                   │                     │                  │
   │  ┌────────────────┼─────────────────────┼──────────────────┐
   │  │              RRF 融合                                 │
   │  │   score = Σ(1/(k + rank_i))                          │
   │  │   k=60, 两路结果合并排序                               │
   │  └────────────────┼─────────────────────┼──────────────────┘
   │                   │                     │
   │  ┌────────────────┼─────────────────────┼──────────────────┐
   │  │           可选 Reranker 精排 (Cross-Encoder)           │
   │  │─────────────────────────────────────────────────────▶│
   │  │◀── reranked top_k ──────────────────────────────────│
   │  └────────────────┼─────────────────────┼──────────────────┘
   │                   │                     │
   │  ┌────────────────┼─────────────────────┐
   │  │  置信度过滤 → 截断 top_k             │
   │  │  Parent-Child 扩展: 命中子块→附加父块 │
   │  └────────────────┼─────────────────────┘
   │                   │
   ◀── RetrieveResponse {chunks, sources}
```

**降级矩阵**:

| 场景 | 行为 | 影响 |
|------|------|------|
| ES ✅ Milvus ✅ | 混合检索 (BM25 + 向量 + RRF) | 最优 |
| ES ✅ Milvus ❌ | BM25 Only | 检索精度下降 |
| ES ❌ Milvus ✅ | 向量 Only | 关键词匹配减弱 |
| Embedding 熔断 | 自动降级 BM25 | 无向量, 仍有 BM25 |
| Reranker 失败 | 使用 RRF 原始排序 | 精排缺失 |
| 全不可用 | 空结果 → 提示转人工 | — |

### 流程 5：端到端交互时序图

```
  CU          Bot:8000       star-conn       AgentUI       Assist:8001
   │              │              │               │               │
   │ ═══ Phase 1: Bot 对话 ═════════════════════════════════════│
   │              │              │               │               │
   │──POST /chat/send─────────▶│               │               │
   │              │              │               │               │
   │              │─ Redis Streams 消费 ────     │               │
   │              │─ LangGraph Agent 处理       │               │
   │              │─ Pub/Sub 通知结果就绪       │               │
   │              │              │               │               │
   │◀──PollResponse (Bot回复)──│               │               │
   │              │              │               │               │
   │ ═══ Phase 2: 转人工 ═══════════════════════════════════════│
   │              │              │               │               │
   │──"我要转人工"────────────▶│               │               │
   │              │              │               │               │
   │              │─ TransferChecker: L1 命中    │               │
   │              │─ transition(BOT→AG_QUEUED)   │               │
   │              │─ LLM 生成转接摘要            │               │
   │              │              │               │               │
   │              │──create_session({sid,        │               │
   │              │   reason, summary, history})─▶               │
   │              │              │               │               │
   │              │◀── {star_sid, poll_url} ─────│               │
   │              │              │               │               │
   │◀──"正在转接" + poll_url ───│               │               │
   │              │              │               │               │
   │              │     [star-conn 内部: 路由→振铃→坐席接受]      │
   │              │              │               │               │
   │ ═══ Phase 3: 坐席接听 + 激活 ═══════════════════════════════│
   │              │              │               │               │
   │              │              │               │ (坐席持久WS在   │
   │              │              │               │  登录时已建立)  │
   │              │              │               │               │
   │              │              │               │──WS {type:     │
   │              │              │               │  session_     │
   │              │              │               │  activated,   │
   │              │              │               │  session_id}  │
   │              │              │               │──transition──▶│
   │              │              │               │  AG_ACTIVE    │
   │              │              │               │               │
   │              │              │               │◀──WS assist_   │
   │              │              │               │  ready +画像  │
   │              │              │               │  +摘要+历史   │
   │              │              │               │               │
   │ ═══ Phase 4: 辅助推送 ══════════════════════════════════════│
   │              │              │               │               │
   │──客户消息────────────────▶│               │               │
   │              │              │──消息显示──▶│               │
   │              │              │               │               │
   │              │              │──POST /notify────────────────▶│
   │              │              │  (202,<10ms) │               │
   │              │              │               │─ 异步编排      │
   │              │              │               │  get_session  │
   │              │              │               │  意图分类+OE   │
   │              │              │               │               │
   │              │              │               │◀──WS assist_   │
   │              │              │               │  push (复用   │
   │              │              │               │  持久WS,带sid) │
   │              │              │               │               │
   │              │              │               │──POST /feedback──────────▶│
   │              │              │               │               │
   │ ═══ Phase 5: Hold/Resume ═══════════════════════════════════│
   │              │              │               │               │
   │              │              │               │──POST /hold──▶│
   │              │              │               │               │──transition  │
   │              │              │               │               │  AG_ON_HOLD │
   │              │              │               │  ┌─60s静音──┐ │            │
   │              │              │               │◀─WS silence ──│            │
   │              │              │               │  └──────────┘ │            │
   │              │              │               │──POST /resume▶│            │
   │              │              │               │               │──transition  │
   │              │              │               │               │  AG_ACTIVE  │
   │              │              │               │               │               │
   │ ═══ Phase 6: 话后小结 + 结束 ══════════════════════════════│
   │              │              │               │               │
   │              │              │               │──POST /review/──────────────▶│
   │              │              │               │  generate     │               │
   │              │              │               │               │──transition  │
   │              │              │               │               │  AG_REVIEWING│
   │              │              │               │               │─ LLM 生成小结│
   │              │              │               │               │              │
   │              │              │               │◀──WS call_summary───────────│
   │              │              │               │               │              │
   │              │              │               │──POST /review/──────────────▶│
   │              │              │               │  submit      │               │
   │              │              │               │               │──transition  │
   │              │              │               │               │  ENDED       │
   │              │              │               │               │              │
   │              │              │               │◀──WS session_ended─────────│
   │              │              │               │  会话数据清理  │               │
```

**各阶段对应流程**: Phase 1→流程1, Phase 2→流程2 Step1, Phase 3→流程2 Step2, Phase 4→流程3, Phase 5/6→US-11/US-12

---

## 五、坐席 UI 与辅助系统交互

坐席 UI 与辅助系统的交互是 **1 个长连接 (WS 推送) + 多个短请求 (REST 控制)** 模式。WS 负责"辅助内容下行"，REST 负责"会话状态上行"。

### 5.0 star-conn ↔ Assist 通信架构

star-conn 与 Assist 之间的通信按模式分为两层：

```
┌────────────────────────────────────────────┐
│  star-conn (Java)                          │
│                                            │
│  BOT 阶段：                                │
│    客户消息 → HTTP → Bot(:8000)             │  ← 不变
│                                            │
│  AGENT 阶段：                              │
│    客户消息 → POST /notify → Assist(:8001)  │  ← 202, <10ms, 不阻塞
│                                            │
│  控制面：HTTP (不变)                        │
│    /session/update, /hold, /resume,        │
│    /review/*, /feedback                    │
│                                            │
└────────────────────────────────────────────┘
                    │
                    ▼
┌────────────────────────────────────────────┐
│  Assist (Python)                           │
│                                            │
│  /notify → 202 → asyncio.Queue             │
│     │                                      │
│     └──▶ 消费协程：                         │
│          1. 加载 session (Redis)            │
│          2. 编排 (LLM+RAG)                 │
│          3. WS 推送                         │
│                                            │
│  过载时：Queue 满 → 503 → star-conn 感知    │
│                                            │
└────────────────────────────────────────────┘
```

**通信模式分类**：

| 交互 | 端点 | 模式 | 频率 | 延迟要求 |
|------|------|------|------|---------|
| 客户消息通知 | `POST /notify` | 异步通知 (202) | 高 (每条客户消息) | 低 (<10ms 响应) |
| 会话状态更新 | `POST /session/update` | 请求-响应 | 低 | 中 |
| 保持/恢复 | `POST /hold` `/resume` | 请求-响应 | 低 | 中 |
| 话后小结 | `POST /review/*` | 请求-响应 | 低 (每会话1次) | 中 |
| 反馈 | `POST /feedback` | 请求-响应 | 中 | 低 |

**为什么不用 Kafka / Redis Streams**：Assist 宕机时辅助链路已断，消息积压无恢复价值。过载时 503 让 star-conn 感知并降级，比 Stream 缓冲更诚实。注意：此处 503 仅针对 /notify 辅助推送，坐席仍正常对话——与 Bot 的"永不拒客"策略不同。

**容错处理**：

| 场景 | 处理方式 |
|------|---------|
| Assist 宕机 | star-conn HTTP 超时/连接失败 → 该会话无辅助，坐席仍可正常对话 |
| Assist 过载 | Queue 满 → 503 → star-conn 降级 (跳过辅助推送) |
| 网络抖动 | 重试 1~2 次，间隔 100ms；仍失败则跳过 |
| 通知乱序 | AGENT 阶段消息间隔 10-60s，实际不会乱序 |

### 5.1 WebSocket 连接建立

坐席上班登录时建连，一个坐席一条 WS，生命周期与上班周期对齐。会话上下文通过消息中的 `session_id` 字段区分。

```
坐席UI ──WS──▶ ws://assist:8001/api/ws/agent/{agent_id}
               ◀── {"type": "connected", "agent_id": "xxx"}
```

连接注册到服务端 `ws_pool`（agent_id → WebSocket），后续所有推送走此通道。心跳 15s 间隔，30s 超时断开。

**为什么按坐席而不是按会话**:
- 一个坐席同时只处理 1 个会话（极少数情况 2-3 个），按会话建连浪费
- 按坐席建连，生命周期与上班对齐，不随会话反复建连/断连
- 会话上下文由消息中 `session_id` 区分，不需要多条连接

### 5.2 消息触发分析 — 两条入口

| 路径 | 触发方 | 流程 |
|------|--------|------|
| **服务端通知** (主路径) | star-connection CF 收到客户消息后发 `POST /api/notify` | CF → Assist → 202 → 异步编排 → WS 推送 |
| **前端手动触发** (备用) | 坐席 UI 通过 WS 发送 `{"type": "customer_message", "message": "..."}` | 前端 → WS → AssistOrchestrator → WS 回推 |

服务端通知主路径：

```
客户消息 → CF(SessionManager.routeMessage) → Netty → AB → 坐席UI 显示
                                       │
                              SmartcsClient.notifyAssist()
                                       │
                              POST /api/notify {session_id, event: "customer_msg"}
                                       │
                              Assist 服务:
                              1. 立即返回 202 Accepted (< 10ms, 不阻塞 CF)
                              2. 消息入 asyncio.Queue
                              3. 消费协程:
                                 a. SessionManager.get_session() 加载完整上下文
                                 b. 意图分类 (Rule+LLM, 3s 超时)
                                 c. 编排执行 (优先 Temporal, 降级 AssistOrchestrator)
                                 d. WS 推送结果
                                       │
                              坐席UI ← WS ← assist_push 消息
```

**为什么用 /notify 而不是 /analyze**: `/analyze` 是同步请求-响应模式，Assist 需 3-8s 编排时间，期间 star-conn 线程被阻塞。`/notify` 采用异步通知模式：star-conn 发完即返回，Assist 内部异步处理，结果通过 WS 独立推送。

### 5.3 推送消息格式

每次分析完成后，坐席 UI 收到 `assist_push` 消息，包含四路并行结果：

```json
{
  "type": "assist_push",
  "session_id": "xxx",
  "trigger": "customer_message",
  "payload": {
    "scripts": [
      {"script_id": "s1", "content": "建议话术...", "tags": ["card_loss"], "priority": 5}
    ],
    "knowledge": [
      {"chunk_id": "c1", "summary": "...", "source": "挂失流程.md", "confidence": "high"}
    ],
    "alerts": [
      {"level": "warning", "category": "compliance", "message": "检测到承诺性语言", "rule_id": "R1"}
    ],
    "recommendations": [
      {"product_id": "p1", "product_name": "分期优享", "reason": "...", "risk_tip": "..."}
    ]
  }
}
```

### 5.4 会话生命周期操作

坐席 UI 通过 REST API 控制会话阶段转换：

```
坐席UI                         Assist服务(:8001)                    SessionManager
  │                                │                                    │
  │──POST /hold───────────────────▶│                                    │
  │  {session_id, agent_id}        │──transition_phase────────────────▶│
  │                                │  AG_ACTIVE → AG_ON_HOLD            │  Redis meta 更新
  │                                │  + 启动60s静音检测                  │  + 启动超时守卫
  │◀── {status:"ok"}──────────────│                                    │
  │                                │                                    │
  │       ...60秒无客户消息...       │                                    │
  │                                │                                    │
  │◀──WS {type:"silence_alert"}──│                                    │
  │  "客户已保持 60 秒无消息"       │                                    │
  │                                │                                    │
  │──POST /resume─────────────────▶│                                    │
  │  {session_id, agent_id}        │──transition_phase────────────────▶│
  │                                │  AG_ON_HOLD → AG_ACTIVE            │  取消静音检测
  │◀── {status:"ok"}──────────────│                                    │
  │                                │                                    │
  │──POST /review/generate───────▶│                                    │
  │  {session_id, agent_id}        │──transition_phase────────────────▶│
  │                                │  AG_ACTIVE → AG_REVIEWING          │
  │                                │  + LLM生成话后小结                  │
  │◀── {summary_id, ...}──────────│                                    │
  │◀──WS {type:"call_summary"}───│  (同时WS推送小结)                   │
  │                                │                                    │
  │──POST /review/submit─────────▶│                                    │
  │  {summary_id, ...}            │──transition_phase────────────────▶│
  │                                │  AG_REVIEWING → ENDED              │
  │◀── {status:"ok"}──────────────│  + WS 会话数据清理 + 取消超时守卫            │
```

### 5.5 隐式反馈

坐席对推送内容的选择行为被记录为反馈信号，用于优化推荐：

| 坐席操作 | action | confidence | 含义 |
|---------|--------|------------|------|
| 直接发送话术 | `accept` | 1.0 | 话术完全匹配 |
| 修改后发送 | `modify` | 0.5 | 方向对但内容需调整 |
| 复制部分内容 | `partial_accept` | 0.3 | 有参考价值但不完全 |
| 忽略 | `reject` | 0.0 | 不相关 |

反馈有 3 秒延迟确认 (H2)，坐席可在 3 秒内通过 `POST /feedback/undo` 撤销。

### 5.6 服务端推送事件汇总

| 事件类型 | 触发条件 | 方向 |
|---------|---------|------|
| `assist_push` | 每次客户消息分析完成 | 服务端 → 坐席UI |
| `call_summary` | 话后小结生成完成 | 服务端 → 坐席UI |
| `session_timeout` | 超时守卫触发 (排队/振铃/会话/话后超时) | 服务端 → 坐席UI |
| `silence_alert` | ON_HOLD 60秒无客户消息 | 服务端 → 坐席UI |
| `session_ended` | 会话 ENDED | 服务端 → 坐席UI (会话数据清理, WS不断连) |
| `heartbeat` | 每15秒 | 服务端 → 坐席UI (保活) |

### 5.7 节流机制

同一会话在 `throttle_window_ms` 内只推送一次辅助结果，但 **critical 级别告警不受节流限制**，立即推送。

---

## 六、用户故事

### US-1：客户账单查询

> **作为** 客户，**我想要** 咨询信用卡账单问题，**以便** 快速了解我的账单详情，不需要等待人工客服。

| # | 角色 | 交互/动作 | 系统行为 |
|---|------|----------|---------|
| 1 | CU | 输入"我上个月账单多少钱" | POST /chat/send → Redis XADD (Streams) → 返回 accepted |
| 2 | SYS | — | Worker XREADGROUP 消费 |
| 3 | SYS | — | Rule 快路匹配 `bill_query` (conf=0.85) |
| 4 | SYS | — | 路由到 business_agent → 调用核心系统账单查询 API |
| 5 | SYS | — | API 返回结构化账单数据 → LLM 组织自然语言回答 (source=api) |
| 6 | SYS | — | TransferChecker: L1 未触发, L2 未触发, L3 未触发 |
| 7 | SYS | — | 结果写入 Redis, 长轮询返回 PollResponse |
| 8 | CU | 看到账单详情 | — |

**验收标准**:
- [ ] 客户发送消息后 5s 内收到回复
- [ ] 个人数据查询 (账单/额度/积分) 走 business_agent → 核心 API，不走 RAG
- [ ] API 不可用时降级为 RAG 知识检索 (提供查询指引，source=retrieval)
- [ ] 回复附带来源标识 (api/retrieval/template)
- [ ] 回复中不包含其他客户数据 (数据隔离)
- [ ] 会话历史正确记录本轮对话

**降级路径**:
- 核心 API 不可用 → RAG 检索 (提供账单查询指引/操作步骤, source=retrieval)
- 核心 API + 检索都不可用 → 意图模板 (source=template, 提示"输入转人工")
- LLM 不可用 → API 数据格式化输出 (source=api_raw)

---

### US-2：客户关键词转人工

> **作为** 客户，**我想要** 直接和真人客服沟通，**以便** 我的问题能得到更专业的处理。

| # | 角色 | 交互/动作 | 系统行为 |
|---|------|----------|---------|
| 1 | CU | 输入"我要转人工" | POST /chat/send → XADD (Streams) → 返回 accepted |
| 2 | SYS | — | Rule 快路匹配 `transfer_agent` (conf=0.95) |
| 3 | SYS | — | 路由到 business_agent → should_transfer=True |
| 4 | SYS | — | TransferChecker: L1 关键词"转人工"命中 |
| 5 | SYS | — | transfer_node: 会话阶段 BOT → AGENT (sub: agent:queued) |
| 6 | SYS | — | StarClient.create_session() → star-connection 排队 |
| 7 | SYS | — | 返回排队位置 + 预估等待时间 |
| 8 | CU | 看到"正在为您转接人工客服，您前面还有 3 位" | — |

**排队体验**:
- 每 15s 更新排队位置（长轮询返回 queue_update 消息）
- 等待超过 60s 触发排队超时，回退 BOT 并提示"当前坐席繁忙，是否继续自助服务"
- 排队期间客户仍可发送消息，转接后消息一并转给坐席

**验收标准**:
- [ ] L1 关键词 ("人工"/"转人工"/"真人"/"投诉") 立即触发转接
- [ ] 会话阶段正确过渡到 AGENT:agent:queued
- [ ] star-connection 返回排队位置和预估等待时间
- [ ] 排队期间客户可继续发送消息
- [ ] star-connection 不可用时，提示"人工客服系统暂不可用"

---

### US-3：客户累积低置信度自动转人工

> **作为** 客户，**我想要** 在反复得不到满意回答时自动转接人工，**以便** 不必手动请求转接。

| # | 角色 | 交互/动作 | 系统行为 |
|---|------|----------|---------|
| 1-3 | CU | 连续 3 轮提问，每轮 Bot 回答置信度低于阈值 | low_confidence_streak 递增到 3 |
| 4 | CU | 输入第 4 个问题 | Worker 消费 |
| 5 | SYS | — | TransferChecker: L1 未命中, L2 未命中, **L3 命中** (streak ≥ 3) |
| 6 | SYS | — | transfer_node → BOT → AGENT:agent:queued |

**验收标准**:
- [ ] 连续 low_confidence_streak 达到阈值 (默认 3) 时自动转接
- [ ] 客户不需要手动输入"转人工"
- [ ] 低置信度定义: primary_confidence < 配置阈值
- [ ] 转接前 Bot 发出提示"正在为您转接更专业的客服"

---

### US-4：坐席接收实时辅助推送

> **作为** 坐席，**我想要** 在接听客户来电时实时收到话术推荐、知识片段和合规提醒，**以便** 更专业高效地服务客户。

| # | 角色 | 交互/动作 | 系统行为 |
|---|------|----------|---------|
| 1 | AG | 在 Workbench 打开会话 | WS 已建立 (登录时 per-agent) → 发送 session_activated → 收到 assist_ready |
| 2 | SYS | — | Assist 收到 WS 消息 → transition(AG_ACTIVE) |
| 3 | CU | 说"我想提升信用卡额度" | star-connection → POST /notify (202) → 异步编排 |
| 4 | SYS | — | 意图分类 → limit_query (conf=0.82) |
| 5 | SYS | — | OE EVALUATING: D1 激活, D2 被 suppress (D1 激活→压制营销), D3 始终激活 |
| 6 | SYS | — | OE DISPATCHING: E1∥E3 并行 |
| 7 | SYS | — | E1: LangGraph DAG → 服务卡片 (话术+知识) |
| 8 | SYS | — | E3: AlertEngine → PASS |
| 9 | SYS | — | 仲裁: PASS → 服务卡片, 营销隐藏 (D2 被压制) |
| 10 | SYS | — | PII 脱敏 → WS 推送 assist_push |
| 11 | AG | 看到提额话术 + 相关知识片段 | — |

**验收标准**:
- [ ] 坐席接听后 <500ms 收到客户画像+Bot 历史摘要 (Redis 直读)
- [ ] 坐席接听后 3-8s 收到首次辅助推送 (LLM+RAG 编排)
- [ ] 推送内容经过 PII 脱敏
- [ ] 服务场景下营销被自动压制
- [ ] WebSocket 断连后自动清理连接池
- [ ] 心跳 15s 间隔, 30s 超时断开

---

### US-5：坐席遇到合规风险告警

> **作为** 坐席，**我想要** 在客户对话中出现合规风险时收到实时警告，**以便** 避免不当承诺造成监管风险。

| # | 角色 | 交互/动作 | 系统行为 |
|---|------|----------|---------|
| 1 | CU | 说"你们能不能保证帮我提额" | POST /notify (202) → 异步编排 |
| 2 | SYS | — | D3 始终激活 → E3 执行 |
| 3 | SYS | — | E3: AlertEngine 检测到"保证" → WARN 级别 |
| 4 | SYS | — | 仲裁: risk_action=WARN → 服务卡片 + 风险徽章 + 营销缩小 |
| 5 | SYS | — | WS 推送 assist_push (告警级别高于节流阈值) |
| 6 | AG | 看到风险徽章"避免过度承诺" | — |

**极端场景**: 客户说"帮我套现" → E3 BLOCK → 仅显示风险卡片, 营销完全隐藏

**验收标准**:
- [ ] 以下合规规则正常触发:

| 规则 | 检测内容 | 级别 | 典型场景 |
|------|---------|------|---------|
| 欺诈承诺 | "包过"/"一定通过" | BLOCK | 审批类咨询 |
| 利率/收益承诺 | "保证年化5%"/"稳赚不赔" | BLOCK | 理财/分期推荐 |
| 超权限承诺 | "一定帮你提额"/"保证批复" | BLOCK | 提额/审批 |
| 诱导套现/违规用卡 | "怎么套现"/"怎么规避风控" | BLOCK | 非法用卡 |
| 费用隐瞒 | 推荐分期未提及手续费 | WARN | 分期营销 |
| 过度承诺 | "保证"/"一定"/"肯定" | WARN | 通用 |
| 身份证号 | 18位身份证号 | BLOCK | PII 泄露 |
| 手机号 | 11位手机号 | BLOCK | PII 泄露 |
| 银行卡号 | 16/19位卡号 | BLOCK | PII 泄露 |
| 脏话/辱骂 | 侮辱性语言 | WARN | 情绪失控 |

- [ ] critical (BLOCK) 级别告警 → 营销卡片完全隐藏，仅显示风险提示
- [ ] warning (WARN) 级别告警 → 营销卡片降级为小卡片，风险徽章标记
- [ ] critical 告警绕过节流机制
- [ ] 合规短语 ("保证收益"/"稳赚不赔"等) 被过滤为 [已过滤]
- [ ] PII (身份证号/手机号/卡号) 检测与脱敏在 E3 和全局仲裁器双重执行

---

### US-6：坐席提供隐式反馈

> **作为** 坐席，**我想要** 对系统推送的建议给出反馈（采纳/修改/拒绝），**以便** 系统后续能提供更精准的建议。

| # | 角色 | 交互/动作 | 系统行为 |
|---|------|----------|---------|
| 1a | AG | 直接使用推送话术 | POST /feedback (action=accept, confidence=1.0) |
| 1b | AG | 修改后使用 | POST /feedback (action=modify, confidence=0.5, modify_fields=[...]) |
| 1c | AG | 忽略推送 | POST /feedback (action=reject, confidence=0.0) |
| 2 | SYS | — | 反馈缓冲 3 秒 (允许撤销) |
| 3a | AG | 3 秒内点击撤销 | POST /feedback/undo → 缓冲区移除 |
| 3b | SYS | 3 秒到期 | CAS 写入 Redis → 反馈日志归档 |

**验收标准**:
- [ ] 4 种反馈动作正确映射置信度
- [ ] 3 秒内可撤销 (undo)
- [ ] 超过 3 秒不可撤销
- [ ] 反馈通过 CAS 写入 Redis，不丢失
- [ ] 并发反馈不冲突

---

### US-7：坐席遇到营销推荐场景

> **作为** 坐席，**我想要** 在客户情绪积极时收到合适的产品推荐，**以便** 适时进行交叉营销。

| # | 角色 | 交互/动作 | 系统行为 |
|---|------|----------|---------|
| 1 | CU | 情绪积极，咨询积分兑换 | POST /notify (202) → 异步编排 |
| 2 | SYS | — | D1 激活 (reward_query, conf>0.5), D2 激活 (情绪积极) |
| 3 | SYS | — | 策略: D1 激活 → D2 被 suppress (2 轮) |
| 4 | SYS | — | E1 执行 → E2 被压制 → 仅推送服务卡片 |
| 5 | CU | 继续询问 (2 轮后) | suppress 计数递减 |
| 6 | SYS | — | suppress 过期, D2 重新激活 → E2 延后 500ms 执行 |
| 7 | AG | 看到分期产品推荐卡片 | — |

**验收标准**:
- [ ] 服务场景自动压制营销 2 轮
- [ ] suppress 过期后营销自动恢复
- [ ] E3 BLOCK 时营销永久跳过 (本轮)
- [ ] 营销卡片延后 500ms 执行 (不抢占服务响应)

---

### US-8：运营上传知识文档

> **作为** 运营，**我想要** 上传知识文档到系统，**以便** 客户和坐席能检索到最新的产品政策和流程指南。

| # | 角色 | 交互/动作 | 系统行为 |
|---|------|----------|---------|
| 1 | OP | 上传 PDF 文档 (multipart) | POST /kb/documents |
| 2 | SYS | — | 校验格式 → MinIO 存储 → 创建 KbDocument (status=PENDING) |
| 3 | SYS | — | 解析 (pymupdf) → 清洗 (去页眉/控制符/去重) → 分块 |
| 4 | SYS | — | FAQ: H2 问答对提取; 结构文档: Parent-Child 层级分块 |
| 5 | SYS | — | 批量嵌入 (Ollama/TEI, batch=128) |
| 6 | SYS | — | 双写: ES (BM25 索引) + Milvus (向量索引) |
| 7 | SYS | — | 可选 Kafka 发布知识更新事件 |
| 8 | OP | 看到入库结果 | {doc_id, chunk_count, status} |

**验收标准**:
- [ ] 支持 6 种格式: PDF, DOCX, HTML, MD, TXT, XLSX
- [ ] FAQ 文档正确提取问答对
- [ ] 结构文档生成 Parent-Child 层级分块
- [ ] 表格不被分块拆断 (header 复制到每个子块)
- [ ] 入库各阶段记录 KbIngestionLog (耗时/状态/详情)
- [ ] 内容 SHA-256 去重

---

### US-9：系统降级自动容灾

> **作为** 系统，**我需要** 在 LLM/Embedding 服务不可用时自动降级，**以便** 客户和坐席仍能获得基本服务。

| 故障场景 | 降级行为 | 用户影响 |
|---------|---------|---------|
| LLM 熔断 (5 次连续失败) | 分类降级为 Rule only; 生成降级为检索摘要 | 回答质量下降但可交互 |
| LLM + 检索都不可用 | 生成降级为意图模板 (10 种意图模板) | 建议客户"输入转人工" |
| Embedding 熔断 | 检索降级为 BM25 Only | 检索精度下降但仍有结果 |
| Temporal 不可用 | Assist 降级为同步编排器 (4 路并行) | 无 OE 策略矩阵但仍有辅助推送 |
| E3 风控熔断 | 降级为 pass_with_audit_flag | 放行但标记待审, 事后审计 |
| E1 AI 熔断 | 降级为 fast_path 或 safe_fallback | 坐席看到兜底话术 |
| E2 营销熔断 | 降级为 skip_card | 营销卡片不展示 |
| 全局超时 (>5s) | 返回 timeout_partial | 坐席收到部分结果 |

**验收标准**:
- [ ] LLM 熔断器: 5 次连续失败打开, 60s 后半开探测, 2 次成功关闭
- [ ] Embedding 熔断器: 30s 探测间隔, 2 次成功恢复, 3 次失败打开
- [ ] 降级切换对用户透明 (不报错, 不中断)
- [ ] 降级恢复后自动升级 (DEGRADED → NORMAL)
- [ ] 所有降级事件记录日志

---

### US-10：客户从 Bot 平滑转接坐席

> **作为** 客户，**我想要** 在需要时无缝从机器人对话切换到人工客服，**以便** 我的完整对话上下文能被人工客服继承。

| # | 角色 | 交互/动作 | 系统行为 |
|---|------|----------|---------|
| 1 | CU | 在 Bot 对话中 | 会话阶段 = BOT, sub = bot:active |
| 2 | SYS | 触发转人工 (L1/L2/L3/业务直转) | transfer_node → AGENT:agent:queued |
| 3 | SYS | — | StarClient.create_session() → star-connection 排队 |
| 4 | SYS | — | star-connection 分配坐席 → agent:assigned (振铃) |
| 5 | AG | 坐席接听 | AgentUI 发送 session_activated → Assist 激活 → assist_ready |
| 6 | SYS | — | OE 编排启动: 话术+知识+告警+产品 推送到坐席 |
| 7 | AG/CU | 对话结束 | → agent:reviewing → ENDED |
| 8 | SYS | — | WS 会话数据清理, 反馈归档, 话后小结提交 |

**验收标准**:
- [ ] 转接后坐席可看到客户与 Bot 的历史对话 (最近 20 轮)
- [ ] 坐席接听后 <500ms 收到客户画像+转接摘要 (Redis 直读)
- [ ] 坐席接听后 3-8s 收到首次辅助推送 (LLM+RAG 编排)
- [ ] 转接摘要包含: 转接原因 + Bot 对话概要 + 客户情绪
- [ ] 会话阶段正确流转: BOT → AGENT(queued→assigned→active→reviewing) → ENDED
- [ ] WS 连接生命周期与坐席上班周期对齐（per-agent 持久连接）
- [ ] ENDED 时 WS 会话数据清理 (WS 本身不断连, 可服务下一个会话)

---

### US-11：坐席保持与恢复 (Hold/Resume)

> **作为** 坐席，**我想要** 在需要时将会话保持，**以便** 处理其他事务后继续服务该客户。

| # | 角色 | 交互/动作 | 系统行为 |
|---|------|----------|---------|
| 1 | AG | 点击"保持"按钮 | POST /hold → AG_ACTIVE → AG_ON_HOLD |
| 2 | SYS | — | 启动 60s 静音检测 |
| 3 | SYS | 60 秒无客户消息 | WS 推送 silence_alert: "客户已保持 60 秒无消息" |
| 4 | AG | 点击"恢复"按钮 | POST /resume → AG_ON_HOLD → AG_ACTIVE |
| 5 | SYS | — | 取消静音检测，恢复正常辅助推送 |

**验收标准**:
- [ ] 保持后静音检测自动启动 (60s 间隔)
- [ ] 静音提醒通过 WS 推送，不阻塞会话
- [ ] 恢复后静音检测自动取消
- [ ] ON_HOLD 超时守卫仍生效 (默认 1800s)

---

### US-12：坐席话后小结 (Review)

> **作为** 坐席，**我想要** 在对话结束后自动生成话后小结，**以便** 减少手动填写的工作量。

| # | 角色 | 交互/动作 | 系统行为 |
|---|------|----------|---------|
| 1 | AG/CU | 对话结束 | → AG_REVIEWING |
| 2 | AG | 点击"生成小结" | POST /review/generate → LLM 生成摘要 |
| 3 | SYS | — | WS 推送 call_summary + HTTP 返回小结内容 |
| 4 | AG | 审核/修改小结 | — |
| 5 | AG | 点击"提交" | POST /review/submit → AG_REVIEWING → ENDED |
| 6 | SYS | — | WS 会话数据清理, 超时守卫取消 (WS 本身不断连) |

**验收标准**:
- [ ] LLM 生成小结包含: 客户需求/问题分类/解决方案/解决状态/情感/关键信息
- [ ] LLM 不可用时降级为模板小结
- [ ] 小结超时 (120s) 自动 ENDED
- [ ] 提交小结后会话正确结束

---

### US-13：会话超时自动处理

> **作为** 系统，**我需要** 在各阶段超时后自动处理会话，**以便** 不占用坐席资源和排队位。

| 超时场景 | 超时值 | 自动行为 | 坐席通知 |
|---------|--------|---------|---------|
| 排队超时 | 60s | AG_QUEUED → BOT (回退自助) | WS: session_timeout |
| 振铃超时 | 30s | AG_ASSIGNED → ENDED | WS: session_timeout |
| 会话时长超时 | 1800s | AG_ACTIVE → ENDED | WS: session_timeout |
| 保持超时 | 1800s | AG_ON_HOLD → ENDED | WS: session_timeout |
| 话后小结超时 | 120s | AG_REVIEWING → ENDED | WS: session_timeout |
| BOT 空闲超时 | 120s | BOT_ACTIVE → ENDED | — |

**验收标准**:
- [ ] 每个子阶段启动时自动创建超时守卫
- [ ] 阶段切换时旧守卫自动取消，新守卫自动启动
- [ ] ENDED 时所有守卫自动清理
- [ ] 超时触发时 WS 推送 session_timeout 事件
- [ ] 超时事件记录 Prometheus SESSION_TIMEOUTS 指标
- [ ] 状态已被其他流程改变时静默跳过 (ValueError catch)

---

## 七、Bot 与 Assist 内部运行机制与技术栈

### 7.1 Bot (:8000) 内部架构

```
┌──────────────────── Bot :8000 ────────────────────────┐
│                                                        │
│  HTTP 线程 (FastAPI/uvicorn)                            │
│  ┌──────────────────────────────────────────────────┐  │
│  │  POST /chat/send                                 │  │
│  │    → XADD chat:stream * (消息落 Redis Stream)    │  │
│  │    → 202 Accepted                                │  │
│  │                                                  │  │
│  │  GET /chat/poll                                  │  │
│  │    → GET response:{sid} (快速路径: 结果已就绪)    │  │
│  │    → SUBSCRIBE notify:{sid} (阻塞等待)            │  │
│  │    → Pub/Sub 唤醒 → GET response → DEL → 返回    │  │
│  └──────────────────────────────────────────────────┘  │
│                                                        │
│  后台协程 (lifespan 启动, 常驻)                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │  _consumer_loop                                  │  │
│  │    while True:                                   │  │
│  │      msgs = XREADGROUP bot-group COUNT 10 BLOCK 1s│  │
│  │      for msg in msgs:                            │  │
│  │        create_task(_process_message(msg))  ← 秒返 │  │
│  │                                                  │  │
│  │  _process_message (per-session Worker, 无锁)             │  │
│  │    1. 幂等: EXISTS response:{sid}? → XACK 跳过   │  │
│  │    2. Queue: 追加入队, 独享 Worker 串行消费     │  │
│  │    3. Semaphore: 有空槽?                          │  │
│  │       Yes → Agent.run() ~2-5s                    │  │
│  │       No  → quick_intent_match() → fast_reply    │  │
│  │    4. SETEX response:{sid} TTL 120s             │  │
│  │    5. PUBLISH notify:{sid} "ready"              │  │
│  │    6. XACK ← 从 PEL 移除                         │  │
│  │                                                  │  │
│  │  _claim_stale (30s 间隔)                          │  │
│  │    XAUTOCLAIM min_idle=60s → 认领挂死消息         │  │
│  └──────────────────────────────────────────────────┘  │
│                                                        │
└────────────────────────────────────────────────────────┘
```

**技术栈**:

| 组件 | 技术 | 用途 |
|------|------|------|
| Web 框架 | FastAPI + uvicorn | HTTP 路由, 长轮询 |
| 消息通道 | Redis Streams (XADD/XREADGROUP/XACK) | 消息持久化, at-least-once |
| 消费者组 | Redis Consumer Group | 消息投递, PEL 记录 |
| 宕机恢复 | XAUTOCLAIM | 超时未确认消息自动认领 |
| 异步编排 | asyncio (create_task / Semaphore / per-session Queue + Worker) | 并发消费, Queue 保序, 过载保护 |
| 通知机制 | Redis Pub/Sub (PUBLISH/SUBSCRIBE) | 轮询即时唤醒 |
| AI 编排 | LangGraph StateGraph | Supervisor 路由 (knowledge/business/fallback) |
| 意图分类 | Rule (regex) + LLM (Qwen2.5) | 双通道, 快路<10ms, 慢路~200ms |
| 熔断降级 | CircuitBreaker (5次失败开, 60s 半开) | LLM/Embedding 不可用自动降级 |
| 会话状态 | SessionManager → Redis | meta + history 分离存储 |

### 7.2 Assist (:8001) 内部架构

```
┌─────────────────── Assist :8001 ───────────────────────┐
│                                                        │
│  WS 线程 (FastAPI/uvicorn)                              │
│  ┌──────────────────────────────────────────────────┐  │
│  │  WS /ws/agent/{agent_id}                         │  │
│  │    坐席登录时建连 → ws_pool[agent_id] = WebSocket │  │
│  │    生命周期 = 坐席上班周期                         │  │
│  │                                                  │  │
│  │  WS 双向消息:                                    │  │
│  │    → AgentUI: assist_push / call_summary /       │  │
│  │              silence_alert / session_timeout     │  │
│  │    ← AgentUI: session_activated / agent_message  │  │
│  └──────────────────────────────────────────────────┘  │
│                                                        │
│  REST 线程                                              │
│  ┌──────────────────────────────────────────────────┐  │
│  │  POST /notify          star-conn 客户消息通知     │  │
│  │    → 202 → asyncio.Queue (秒返)                  │  │
│  │  POST /hold|/resume    坐席会话保持/恢复          │  │
│  │  POST /review/*        话后小结                   │  │
│  │  POST /feedback        隐式反馈                   │  │
│  └──────────────────────────────────────────────────┘  │
│                                                        │
│  后台协程 (lifespan 启动, 常驻)                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │  _notify_consumer_loop                           │  │
│  │    while True:                                   │  │
│  │      sid = await _event_queue.get()              │  │
│  │      create_task(_handle_notify(sid))  ← 秒返    │  │
│  │                                                  │  │
│  │  _handle_notify (per-session Worker, 无锁)               │  │
│  │    1. 幂等: EXISTS notify:{sid}:{ts} 跳过        │  │
│  │    2. Queue: 追加入队, 独享 Worker 串行消费     │  │
│  │    3. SessionMgr.get_session(sid) → 完整上下文    │  │
│  │    4. 意图分类 (Rule+LLM) ~200ms                 │  │
│  │    5. OE 编排 (优先 Temporal, 降级同步) ~2-5s     │  │
│  │       D1(服务) D2(营销) D3(风控) → 策略矩阵       │  │
│  │       E1∥E3 并行 → 仲裁 → PII脱敏                │  │
│  │    6. WS 推送 → ws_pool[agent_id].send_json()    │  │
│  │    7. 隐式反馈: 监听坐席回复 → 推断采纳/修改     │  │
│  │    8. 合规检测: 坐席消息实时检测 → WARN/BLOCK     │  │
│  └──────────────────────────────────────────────────┘  │
│                                                        │
│  兜底协程                                               │
│  ┌──────────────────────────────────────────────────┐  │
│  │  _timeout_guard (asyncio.Task per sub-phase)     │  │
│  │    超时 → transition → WS push session_timeout    │  │
│  │  _silence_detector (AG_ON_HOLD, 60s 无消息)       │  │
│  │    → WS push silence_alert                       │  │
│  └──────────────────────────────────────────────────┘  │
│                                                        │
└────────────────────────────────────────────────────────┘
```

**技术栈**:

| 组件 | 技术 | 用途 |
|------|------|------|
| Web 框架 | FastAPI + uvicorn | HTTP + WebSocket |
| 消息入口 | HTTP POST /notify → asyncio.Queue | star-conn 异步通知 |
| 异步编排 | asyncio (create_task / Queue / per-session Worker) | 并发消费, Worker 保序 |
| OE 编排 | Temporal Workflow (主) / AssistOrchestrator (降级) | D评估→策略矩阵→E执行→仲裁 |
| 会话状态 | SessionManager → Redis | 与 Bot 共享同一 Redis |
| WS 管理 | ws_pool: dict[agent_id, WebSocket] | 按坐席索引, 一条持久 WS |
| 合规检测 | AlertEngine (正则+LLM) | 10 条银行合规规则, <100ms |
| 话术服务 | ScriptService (DB+内存) | 话术模板检索 |
| 风控 | BLOCK/WARN/PASS 三级 | E3 执行器, 始终激活 |
| 超时守卫 | asyncio.Task per sub-phase | 6 种超时, 阶段切换自动重建 |
| 静音检测 | asyncio.Task (60s) | AG_ON_HOLD 时启动 |
| PII 脱敏 | 全局仲裁器递归处理 | 身份证/手机号/卡号 |

## 八、关键设计决策

| # | 决策 | 选择 | 原因 | 待验证 |
|---|------|------|------|--------|
| 1 | 会话状态模型 | 3 Phase × 7 SubPhase | 层级清晰，子阶段驱动超时/推送策略 | 子阶段是否足够覆盖所有场景 |
| 2 | Bot 消息通道 | Redis Streams + Consumer Group + PEL | 持久化保证 at-least-once，XACK 确认，宕机消息留 PEL 可重试 | per-session Queue 内存占用 |
| 3 | 双通道分类 | Rule 快路 + LLM 慢路 | 平衡速度与精度, 规则覆盖高频场景 | 规则覆盖率是否足够 |
| 4 | 转人工三级触发 | L1 > L2 > L3 | 优先级明确，高级别直接触发避免延迟 | L3 阈值 3 轮是否合理 |
| 5 | OE 状态机 | Temporal Workflow | 可观测+可恢复+策略矩阵; 不可用同步降级 | Temporal 运维复杂度 |
| 6 | 营销压制 | D1 激活 → D2 suppress 2 轮 | 避免服务场景过度营销 | 2 轮阈值是否合理 |
| 7 | 反馈缓冲 | 3 秒延迟提交 + Undo | 坐席误操作可撤销, 减少脏数据 | 3s 是否太短 |
| 8 | CAS 乐观锁 | Redis Lua 脚本 | 多执行器并发写回状态, 避免丢失更新 | 高并发下冲突率 |
| 9 | PII 脱敏 | 全局仲裁器递归处理 | 确保所有输出经过脱敏 | 正则是否遗漏 |
| 10 | 双写索引 | ES + Milvus | BM25 和向量各有优势, RRF 融合互补 | 一致性保障 |
| 11 | 超时守卫 | asyncio.Task per sub-phase | 细粒度超时，阶段切换自动重建 | 大量会话时 Task 调度开销 |
| 12 | 坐席交互 | 1 条 WS (按坐席,登录建连) + REST 控制 | 生命周期与上班对齐，不随会话反复重建，会话上下文由消息中 sid 区分 | WS 断连恢复策略 |
| 13 | star-conn→Assist 通信 | HTTP /notify (202 异步) | 不阻塞 star-conn 线程，零新增中间件，过载时 503 比 Stream 缓冲更诚实 | 高并发下 asyncio.Queue 吞吐 |
| 14 | Bot 过载保护 | Semaphore(10) + 固定话术兜底 | 满荷不走 Agent，<50ms 返回固定话术，不拒客、不堆积 | 固定话术覆盖率 |

---

## 九、待讨论议题

### 高优先级

1. **L3 累积转人工阈值**: 当前 3 轮连续低置信度，是否需要更灵活的策略？
2. **star-connection 容错**: 转人工桥接失败时的重试/降级策略
3. **合规规则动态化**: 运营后台增删合规规则的能力
4. **反馈闭环**: 隐式反馈数据如何用于优化推荐质量
5. **客户身份核验**: 查询个人数据 (账单/额度) 前是否需要身份核验 (如短信验证码)
6. **business_agent 核心API 对接**: 账单/额度/积分/分期等查询接口的协议与鉴权

### 中优先级

7. **文档审核流程**: 知识文档上传后是否需要审核环节
8. **推送暂停**: 坐席端"暂停推送"按钮的需求
9. **降级状态可视化**: 运营/坐席端展示当前降级状态
10. **WS 断连恢复**: WebSocket 意外断开后是否需要自动重连+消息补发
11. **排队超时回退**: 排队超时回退 BOT 后是否需要通知客户
12. **坐席转接**: 坐席 A 将会话转给坐席 B (如专业组/升级处理) 的流程
13. **客户主动挂断**: 客户在 AGENT 阶段主动断开时的清理流程

### 低优先级

14. **入库异步化**: 大文档后台处理 + 进度查询
15. **文档增量更新**: 新版本只更新变更分块
16. **文档过期下架**: expiry_date 自动下架机制
17. **坐席显式反馈**: 补充主动评分机制
