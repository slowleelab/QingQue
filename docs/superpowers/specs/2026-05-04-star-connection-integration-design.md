# star-connection 集成到 SmartCS 在线客服系统 — 完整设计方案

> 日期：2026-05-04
> 状态：待审批
> 上游文档：智能客服实施计划.md、Sprint5-操作笔记.md

---

## 1. 目标

将 star-connection（Java/Spring Boot/Netty/ZooKeeper 星型长连接在线客服系统）嵌入到 SmartCS 项目中，作为人工客服系统，与 SmartCS Bot（AI 应答）和 SmartCS Assist（AI 坐席辅助）对接，形成**全链路智能客服闭环**。

---

## 2. 整体架构

```
客户（浏览器/App）
     │
     ▼  HTTP 长轮询
┌──────────────────────┐
│   SmartCS Bot :8000   │  AI 应答层
│   POST /api/chat/send │  发消息 → 入队 → 异步处理
│   GET  /api/chat/poll │  长轮询 → 读 Redis → 返回结果
└──────┬───────────────┘
       │ 转人工
       │ POST /api/sessions
       ▼
┌──────────────────────┐
│   star-connection     │  在线客服层（嵌入到项目）
│   customer-server     │  客户 HTTP 长轮询端点
│   agent-server        │  坐席 WebSocket 端点
│   ZooKeeper           │  服务发现 + 坐席绑定
└──────┬───────────────┘
       │ 旁路监听
       ▼  WS
┌──────────────────────┐
│   SmartCS Assist :8001│  AI 辅助层
│   WS /api/ws/{sid}    │  话术 + 知识 + 告警
└──────────────────────┘
```

### 两种模式

| 模式 | 流程 | 入口 |
|------|------|------|
| **Bot 先行** | 客户 → Bot AI 应答 → 转人工 → star-connection → 坐席 | `GET /api/chat/poll` |
| **直接人工** | 客户 → star-connection 创建会话 → 分配坐席 → AI 旁路 | `GET /customer/poll` |

前端根据是否有 `transfer_url` 自动切换。

---

## 3. 统一通信协议：HTTP 长轮询

所有客户端 ↔ 服务端交互统一使用 HTTP 长轮询。

### 3.1 Bot 阶段（SmartCS）

```
POST /api/chat/send
Request:  {session_id, message, customer_id}
Response: {accepted: true, message_id: "msg-001"}

GET /api/chat/poll?session_id=xxx&since=msg-001
Response:
  无新消息: {has_message: false}
  有回复:   {has_message: true, reply: "您好...", is_transfer: false}
  转人工:   {has_message: true, is_transfer: true, transfer_url: "http://...", transfer_reason: "complaint"}
```

后端：消息入 Redis 队列 → async Agent 处理 → 结果写入 `smartcs:response:{session_id}` → poll 时读取。

### 3.2 人工客服阶段（star-connection）

```
POST /customer/send
Request:  {session_id, content}
Response: {accepted: true, message_id: "msg-002"}

GET /customer/poll?session_id=xxx&token=xxx&since=msg-002
Response:
  无新消息: {has_message: false}
  有新消息: {has_message: true, messages: [{sender: "agent", content: "您好...", timestamp: ...}]}
  会话结束: {has_message: true, session_ended: true}
```

### 3.3 前端轮询状态机

```
        ┌──────────────┐
        │  BOT_POLLING  │  GET /api/chat/poll
        └──────┬───────┘
               │ is_transfer=true
               ▼
        ┌──────────────┐
        │ AGENT_POLLING │  GET /customer/poll
        └──────┬───────┘
               │ session_ended=true
               ▼
        ┌──────────────┐
        │   FINISHED    │
        └──────────────┘
```

---

## 4. 会话状态管理

**单一真相源：SmartCS Redis 的 `SessionState`。**

star-connection 复用同一个 `session_id`，不单独维护对话历史。

### 4.1 状态流转

```
BOT  →  HANDOFF  →  ASSIST  →  ENDED
```

| 阶段 | 负责方 | 说明 |
|------|--------|------|
| `BOT` | SmartCS Bot | AI 对话中 |
| `HANDOFF` | SmartCS → star-connection | 转人工进行中（创建会话、分配坐席） |
| `ASSIST` | star-connection | 坐席接管，AI 辅助旁路 |
| `ENDED` | star-connection | 坐席结束会话 |

### 4.2 转人工流程

```
1. Bot 判定转人工
   → Redis SessionState.phase = HANDOFF
   → POST http://star-connection:8080/api/sessions
      {session_id, customer_id, transfer_reason, transfer_summary, history, intent, sentiment}
   → star-connection 创建 Session(status=WAITING)，分配坐席
   → 返回 {session_id, poll_url, send_url, token, status}

2. star-connection 分配坐席成功 → 会话 ACTIVE
   → 回调 POST http://localhost:8001/api/session/update
      {session_id, phase: "ASSIST", agent_id: "agent-3"}
   → SmartCS Redis 更新 phase=ASSIST, agent_id="agent-3"
   → start Assist WS 监听

3. 坐席结束会话
   → 回调 POST /api/session/update
      {session_id, phase: "ENDED"}
   → SmartCS Redis 更新 phase=ENDED
   → 清理 Assist WS 连接
```

### 4.3 新增端点

```
SmartCS 新增:
  POST /api/session/update  接收 star-connection 的状态回调
  Request:  {session_id, phase, agent_id?}
  Response: {status: "ok"}

star-connection 新增:
  POST /api/sessions         接收 Bot 的转人工请求
  Request:  {session_id, customer_id, transfer_reason, transfer_summary, history, intent, sentiment}
  Response: {session_id, poll_url, send_url, token, status}
```

---

## 5. AI 辅助旁路

star-connection agent-server 在会话进入 ASSIST 阶段后，内部连接 SmartCS Assist WebSocket。

```
agent-server 会话 ACTIVE
  → AssistClient 连接 ws://assist:8001/api/ws/{session_id}
  → 每收到客户消息（HTTP 长轮询）
  → 转发: {type: "customer_message", message, intent, sentiment}
  → 收到 assist_push: {scripts, knowledge, alerts, recommendations}
  → 追加到坐席 WS 推送中
```

AssistClient 生命周期与会话一致：会话创建 → connect；会话结束 → disconnect。

---

## 6. 项目搬迁布局

```
agent_project/
├── star-connection/                # ← 从外部搬迁
│   ├── pom.xml                     # 父 POM
│   ├── common/                     # 共享消息模型
│   ├── transport/                  # 传输层（Netty + ZK）
│   ├── customer-server/            # 客户前置
│   │   └── + conroller/TransferController.java  # 新增: POST /api/sessions
│   ├── agent-server/               # 坐席后台
│   │   └── + assist/AssistClient.java           # 新增: Assist WS 连接
│   └── start.sh
├── deploy/
│   └── docker-compose.yml          # +zookeeper 服务
├── src/smartcs/
│   ├── services/bot/router.py      # 改: /api/chat → /api/chat/send + /api/chat/poll
│   └── services/common/
│       └── deps.py                 # +star_connection client
├── web/
│   └── src/
│       └── composables/
│           └── useChatPoll.ts      # 新增: 统一长轮询 hook
└── Makefile                        # +star-build, star-up, star-down
```

---

## 7. Docker Compose 变更

```yaml
# 新增 ZK 服务
zookeeper:
  image: zookeeper:3.8
  ports:
    - "2181:2181"

# star-connection 模块通过 Java 进程运行，不在 Docker 中
# 由 Makefile 控制: make star-build && make star-up
```

---

## 8. 改造清单

### SmartCS 后端（Python）

| 任务 | 文件 | 内容 |
|------|------|------|
| Bot 改为长轮询 | `bot/router.py` | `POST /api/chat/send` + `GET /api/chat/poll` |
| 轮询响应存储 | `common/redis_client.py` | `smartcs:response:{sid}` 键操作 |
| 转人工桥接 | `bot/router.py` | `POST star-connection/api/sessions` 获取 poll_url |
| 会话回调端点 | 新建或在现有路由 | `POST /api/session/update` |
| star-connection client | `common/star_client.py` | HTTP client 封装 |

### SmartCS 前端（Vue）

| 任务 | 文件 | 内容 |
|------|------|------|
| 统一长轮询 hook | `composables/useChatPoll.ts` | 支持 Bot/Agent 两种模式切换 |
| ChatResponse 类型 | `api/types.ts` | 新增 `transfer_url`, `is_transfer` 字段 |
| BotChat 页面改造 | `views/BotChat.vue` | 从单次 fetch 改为轮询 |

### star-connection（Java）

| 任务 | 文件 | 内容 |
|------|------|------|
| 转人工入口 API | `customer-server/.../TransferController.java` | `POST /api/sessions` 创建会话 |
| Assist WS 客户端 | `agent-server/.../AssistClient.java` | 连接 SmartCS Assist WebSocket |
| 会话状态回调 | `agent-server/.../SessionCallback.java` | 回调 `POST /api/session/update` |

### 基础设施

| 任务 | 内容 |
|------|------|
| Docker Compose | 添加 ZooKeeper 服务 |
| Makefile | `make star-build`, `make star-up`, `make star-down` |
| 依赖安装 | 确保 Maven 3.9+ / Java 17+ 可用 |

---

## 9. 端到端流程验证

```
1. 客户打开页面 → POST /api/chat/send "你好"
2. 轮询 GET /api/chat/poll → 收到 Bot 回复
3. 客户说"我要投诉" → 轮询返回 is_transfer=true + transfer_url
4. 前端切换到 GET /customer/poll
5. 坐席在 star-connection 面板看到新会话
6. 坐席发送回复 → 客户轮询收到
7. Assist 面板显示话术推荐和告警
8. 坐席结束会话 → 轮询返回 session_ended=true
```
