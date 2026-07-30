# 灵智（Lumio）API 参考

> Bot Service（`:8000`）与 Assist Service（`:8001`）的 REST / WebSocket 接口。
>
> 所有 REST 接口前缀均为 `/api`。交互式文档见服务启动后的 `/docs`（Swagger UI）。
>
> 端点路径以 `agent/lumio/services/bot/router.py` 与 `agent/lumio/services/common/auth_router.py` 为准。

## 目录

- [通用约定](#通用约定)
- [Bot Service :8000](#bot-service-8000)
- [Assist Service :8001](#assist-service-8001)
- [认证与管理（两服务共有）](#认证与管理两服务共有)
- [知识库管理（admin）](#知识库管理admin)
- [WebSocket 协议](#websocket-协议)

---

## 通用约定

### 统一错误格式

所有错误返回统一结构，HTTP 状态码由错误码层级映射（基类 `LumioError`，24 个子类）：

```json
{
  "error": {
    "code": 2001,
    "message": "请求参数校验失败",
    "type": "RequestValidationError"
  },
  "request_id": "req-abc123"
}
```

### 错误码层级

| 区间 | 类别 | 示例 HTTP 状态 |
|------|------|----------------|
| 2xxx | 输入校验错误 | 400 |
| 3xxx | 业务规则错误 | 409 / 422 |
| 4xxx | 外部依赖错误 | 502 / 503 |
| 5xxx | 系统内部错误 | 500 |

### 权限模型

- **public**：无需鉴权（如健康检查）
- **agent**：需要普通坐席或更高 JWT
- **admin**：必须 `role=admin` JWT
- 全部 `/api/admin/*` 与 `/api/auth/users` 系列仅 admin 访问

### 健康检查（两服务共有）

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/api/health` | public | 服务健康 + 依赖状态 |
| GET | `/api/health/live` | public | 存活探针（K8s liveness） |
| GET | `/api/health/ready` | public | 就绪探针（K8s readiness） |

---

## Bot Service :8000

### 对话

#### `POST /api/chat/send` · public

发送客户消息（异步处理，响应入队）。

**请求**
```json
{
  "session_id": "可选，缺省自动生成",
  "message": "我要挂失信用卡"
}
```

**响应** `200`
```json
{
  "session_id": "a1b2c3...",
  "message_id": "d4e5f6...",
  "status": "accepted"
}
```

> 输入会先做敏感词检测；`session_id` 为空时自动生成 UUID。

#### `GET /api/chat/poll` · public

长轮询拉取机器人响应。

**参数**：`session_id`（query，必填）

**响应**：有消息时返回机器人回复，超时返回空。

#### `POST /api/chat/end` · public

结束当前会话（`session_id` in body）。

#### `POST /api/chat/transfer` · public

转人工（命中转人工意图时由 Bot Agent 触发，或由客户显式请求）。内部调用 `LumioClient`（Java star-connection）创建人工会话。

#### `POST /api/chat/feedback` · public

客户对回答的反馈（点赞/点踩）。

### 会话控制

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| `POST` | `/api/session/update` | agent | 更新会话阶段 / 子状态（star-connection 回调） |
| `POST` | `/api/hold` | agent | 坐席保持（AG_ACTIVE → AG_ON_HOLD） |
| `POST` | `/api/resume` | agent | 恢复（AG_ON_HOLD → AG_ACTIVE） |
| `POST` | `/api/transfer-to-bot` | agent | 会话从人工转回 Bot |

### 会话查询

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| `GET` | `/api/sessions` | agent | 会话列表（按时间倒序） |
| `GET` | `/api/sessions/{session_id}/messages` | agent | 会话消息历史 |

### 知识库（Bot 端只读）

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| `POST` | `/api/kb/retrieve` | agent | 混合检索（BM25+向量+RRF），支持降级模式 |
| `POST` | `/api/kb/search` | agent | 坐席侧关键词检索（ES IK 分词） |

**`POST /api/kb/retrieve` 请求**
```json
{
  "query": "年费减免政策",
  "top_k": 5,
  "mode": "hybrid"
}
```
`mode` 取值：`hybrid`（默认）/ `bm25_only` / `vector_only`（降级路径）。

---

## Assist Service :8001

### 实时分析

#### `POST /api/analyze` · agent

触发坐席辅助分析（OE Pipeline → 仲裁 → 推送）。通常由通话事件驱动。

**请求**
```json
{
  "session_id": "sess-1",
  "trigger": "customer_message",
  "text": "我想申请账单分期"
}
```

#### `POST /api/notify` · agent

外部系统通知（如 star-connection 的话务事件、坐席状态变更）。

#### `POST /api/session/update` · agent

更新会话阶段 / 子状态（状态机推进）。由 star-connection 回调触发。

### 话后处理（After-Call Work）

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| `POST` | `/api/review/generate` | agent | 生成通话小结（`ReviewResponse`） |
| `POST` | `/api/review/submit` | agent | 坐席确认提交小结 |

### 反馈闭环

#### `POST /api/feedback` · agent

记录坐席隐式反馈，写入 Redis 缓冲，**3 秒延迟提交**，期间可撤销。

**请求**
```json
{
  "session_id": "sess-1",
  "agent_id": "agent-001",
  "action": "accept",
  "modify_fields": ["script_content"]
}
```

`action` 取值与置信度映射：

| action | confidence | 含义 |
|--------|-----------|------|
| `accept` | 1.0 | 直接发送 |
| `modify` | 0.5 | 修改后发送（带 `modify_fields`） |
| `partial_accept` | 0.3 | 部分采用 |
| `reject` | 0.0 | 忽略 |

**响应**
```json
{ "status": "ok", "action": "accept", "confidence": 1.0, "delayed_commit": true }
```

#### `POST /api/feedback/undo` · agent

撤销 3 秒延迟期内缓冲的反馈。

**响应**：`{ "status": "ok", "undone": true }`（已提交则 `undone: false`）。

---

## 认证与管理（两服务共有）

> 管理接口需 JWT。`/api/auth/login` 获取 token；`/api/auth/users` 系列仅 admin。

### 认证

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| `POST` | `/api/auth/login` | public | 登录获取 JWT（生产对接 LDAP/SSO） |
| `GET` | `/api/auth/me` | agent | 当前用户信息 |
| `GET` | `/api/auth/users` | admin | 用户列表 |
| `POST` | `/api/auth/users` | admin | 新建用户 |
| `PUT` | `/api/auth/users/{user_id}` | admin | 更新用户 |
| `DELETE` | `/api/auth/users/{user_id}` | admin | 删除用户 |

**`POST /api/auth/login` 请求**
```json
{ "user_id": "agent-001", "role": "agent", "password": "" }
```
`role`：`customer` / `agent` / `admin`。

### 管理

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| `GET` | `/api/admin/stats` | admin | 业务统计 |
| `GET` | `/api/admin/dead-letter` | admin | 死信队列查看 |
| `POST` | `/api/admin/rules/reload` | admin | 触发 L1 意图规则热加载 |
| `GET` | `/api/admin/sensitive-words` | admin | 敏感词列表 |
| `PUT` | `/api/admin/sensitive-words` | admin | 更新敏感词（Pub/Sub 热加载） |

---

## 知识库管理（admin）

### 文档（kb_document）

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| `GET` | `/api/kb/documents` | agent | 文档列表 |
| `POST` | `/api/kb/documents` | admin | 上传知识文档（md/txt） |
| `DELETE` | `/api/kb/documents/{doc_id}` | admin | 删除文档 |
| `GET` | `/api/kb/documents/{doc_id}/status` | agent | 文档处理状态（ingestion pipeline） |
| `GET` | `/api/kb/documents/{doc_id}/approvals` | agent | 审批记录 |
| `POST` | `/api/kb/documents/{doc_id}/submit` | agent | 提交审批 |
| `POST` | `/api/kb/documents/{doc_id}/approve` | admin | 审批通过 |
| `POST` | `/api/kb/documents/{doc_id}/reject` | admin | 驳回 |
| `POST` | `/api/kb/documents/{doc_id}/publish` | admin | 发布 |
| `POST` | `/api/kb/documents/{doc_id}/archive` | admin | 归档 |

### FAQ（kb_faq）

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| `GET` | `/api/kb/faq` | agent | FAQ 列表 |
| `POST` | `/api/kb/faq` | admin | 新建 FAQ |
| `GET` | `/api/kb/faq/{faq_id}` | agent | FAQ 详情 |
| `PUT` | `/api/kb/faq/{faq_id}` | admin | 更新 FAQ |
| `POST` | `/api/kb/faq/batch` | admin | 批量导入 FAQ |
| `POST` | `/api/kb/faq/search` | agent | FAQ 关键词检索 |
| `POST` | `/api/kb/faq/{faq_id}/submit` | agent | 提交审批 |
| `POST` | `/api/kb/faq/{faq_id}/approve` | admin | 审批通过 |
| `POST` | `/api/kb/faq/{faq_id}/reject` | admin | 驳回 |
| `POST` | `/api/kb/faq/{faq_id}/publish` | admin | 发布 |
| `POST` | `/api/kb/faq/{faq_id}/archive` | admin | 归档 |

---

## WebSocket 协议

Assist Service 提供两个 WebSocket 端点，消息均为 JSON。

### `WS /api/ws/{session_id}` — 会话通道

**客户端 → 服务端**

| type | 说明 | 附加字段 |
|------|------|----------|
| `ping` | 心跳 | — |
| `customer_message` | 客户消息触发辅助分析 | `message` 等 |

**服务端 → 客户端**

| type | 说明 |
|------|------|
| `pong` | 心跳响应 |
| `assist_ready` | 通道就绪 |
| `assist_push` | 辅助推送（话术卡/知识/告警/推荐） |
| `call_summary` | 通话小结 |
| `silence_alert` | 静默提醒 |
| `session_ended` | 会话结束 |
| `error` | 错误（如无效 JSON） |

**`assist_push` 载荷示例**
```json
{
  "type": "assist_push",
  "session_id": "sess-1",
  "timestamp": "2026-07-29T08:00:00Z",
  "trigger": "customer_message",
  "payload": {
    "scripts": [{ "script_id": "s1", "content": "您好，请问...", "tags": ["faq"], "priority": 5 }],
    "knowledge": [],
    "alerts": [],
    "recommendations": []
  }
}
```

### `WS /api/ws/agent/{agent_id}` — 坐席通道

坐席长连接，接收分配到的会话事件。

**服务端 → 客户端**：`connected`（连接确认）、`assist_push`、`session_activated` 等。

---

## 相关文档

- [系统架构](./architecture.md) — 数据流与状态机
- [配置参考](./configuration.md) — 超时 / 阈值等可调参数
