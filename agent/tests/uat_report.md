# SmartCS 用户验收测试报告

**日期：** 2026-05-24
**测试人员：** 自动化 QA
**环境：** 本地开发环境（全服务运行）

---

## 测试概要

| 编号 | 测试项 | 端点 | 结果 | HTTP | 说明 |
|------|--------|------|------|------|------|
| 1a | 发送账单查询 | POST /api/chat/send | 通过 | 200 | 创建会话成功 |
| 1b | 轮询账单回复 | GET /api/chat/poll | 通过 | 200 | 意图正确：bill_query |
| 1c | 发送转人工请求 | POST /api/chat/send | 通过 | 200 | 转接请求已接受 |
| 1d | 轮询转接回复 | GET /api/chat/poll | 通过 | 200 | transfer_agent, is_transfer=true |
| 2 | Bot 健康检查 | GET /api/health | 通过 | 200 | 含 Streams 指标 + Agent 槽位 |
| 3 | Assist 异步通知 | POST /api/notify | 通过 | 200 | 通知已接受 |
| 4 | Assist 健康检查 | GET /api/health | 通过 | 200 | 服务健康 |
| 5a | star-conn CF 监控 | GET /api/monitor/customer-service/stats | 警告 | 200 | 返回 SPA 前端页面，非 JSON API |
| 5b | star-conn AB 监控 | GET 同上路径 (8081 端口) | 失败 | 404 | 该端口无非监控端点 |
| 6a | 空消息发送 | POST /api/chat/send | 通过 | 200 | 无输入校验，空消息被接受 |
| 6b | 无效会话轮询 | GET /api/chat/poll (无效sid) | **已修复** | — | 修复前永久挂起，修复后 5s 超时返回 |
| 6c | 缺少必填字段 | POST /api/notify (无session_id) | 通过 | 422 | 正确返回参数校验错误 |

**总评：9/12 通过（75%）**，1 个 Bug 已修复

---

## 详细结果

### 测试 1：Bot 对话流程 — 全部通过

#### 1a. 发送账单查询消息

```json
{"accepted":true,"message_id":"0e4d85b26e4b44c482017e239ae28305","session_id":"5d86d15a9f0547faab7adb313ecec75e"}
```

- **状态码：** 200
- **结论：** 通过 — 消息被 Streams 队列接受，返回 session_id

#### 1b. 轮询获取账单查询回复

```json
{"status":"done","reply":"根据相关信息：[1] 信用卡账单查询：您可以通过手机银行APP、网上银行或拨打客服热线查询本期账单和未出账单。账单日为每月5日，还款日为账单日后25天。","intent":"bill_query","confidence":0.85,"source":"retrieval"}
```

- **状态码：** 200
- **结论：** 通过 — 意图正确识别为 bill_query，置信度 0.85，RAG 检索生成回复

#### 1c. 发送转人工请求

```json
{"accepted":true,"message_id":"b34e382ccd9644e2adc08327f92375c7","session_id":"420f2f657d51405396ac998b26d53ca5"}
```

- **状态码：** 200
- **结论：** 通过 — 转接请求被接受

#### 1d. 轮询获取转接回复

```json
{"status":"done","reply":"检测到您需要办理的业务，正在为您转接人工客服，请稍候。","intent":"transfer_agent","confidence":0.95,"source":"template","is_transfer":true,"transfer_url":"","transfer_reason":"客户主动请求"}
```

- **状态码：** 200
- **结论：** 通过 — 转接意图正确（transfer_agent），is_transfer 标志位正确，转接原因清晰

---

### 测试 2：Bot 健康检查与监控 — 通过

```json
{"status":"healthy","service":"bot","agent_slots":{"total":10,"available":8},"streams":{"pending":2,"stream_length":5,"active_workers":5,"semaphore_utilization":0.2},"stats":{"fast_reply_total":0,"timeout_total":0}}
```

- **状态码：** 200
- **结论：** 通过 — 健康检查返回了 Streams 指标（PEL 待处理数、Stream 长度、活跃 Worker 数、Semaphore 利用率）和统计（fast_reply 累计、timeout 累计）

---

### 测试 3：Assist 异步通知 — 通过

```json
{"status":"accepted"}
```

- **状态码：** 200
- **结论：** 通过 — 通知被接受。注意：设计文档要求返回 202 Accepted，实际返回 200，属于 HTTP 语义层面差异，功能正确。

---

### 测试 4：Assist 健康检查 — 通过

```json
{"status":"healthy","service":"assist"}
```

- **状态码：** 200
- **结论：** 通过

---

### 测试 5：star-connection 监控 — 警告/失败

#### 5a. CF 端口 8080

返回 SPA 前端 HTML 页面（Vue/React 客户端渲染），非 JSON API。CF 主要提供前端界面，监控 API 可能在其他路径。

- **结论：** 警告 — 服务可达，但路径返回前端页面

#### 5b. AB 端口 8081

```json
{"timestamp":"2026-05-23T16:05:56.950+00:00","status":404,"error":"Not Found","path":"/api/monitor/customer-service/stats"}
```

- **结论：** 失败 — AB 端口不存在该监控端点，需确认正确的监控路径

---

### 测试 6：边界条件 — 通过/已修复

#### 6a. 空消息发送

```json
{"accepted":true,"message_id":"02a56fd7898f48dc890a0585ad1d5e36","session_id":"d56958d1ee8f48e1bc756532c9eff67e"}
```

- **状态码：** 200
- **结论：** 通过 — 空消息未被拒绝。是否需要增加输入校验取决于产品需求

#### 6b. 无效会话轮询（已修复）

- **问题：** 用不存在的 session_id 轮询时，Pub/Sub 订阅永久阻塞
- **修复：** 使用 `asyncio.wait_for(timeout + 2s)` 包裹 Pub/Sub 监听循环
- **验证：** 修复后无效会话在 ~5s 内正确返回超时响应

#### 6c. 缺少必填字段

```json
{"error":{"code":2000,"message":"请求参数校验失败","type":"RequestValidationError","details":[{"type":"missing","loc":["body","session_id"],"msg":"Field required","input":{"message":"no session id here"}}]}}
```

- **状态码：** 422
- **结论：** 通过 — 正确返回参数校验错误，错误码 2000（输入校验），含字段级详情

---

## 缺陷报告

### BUG-1：无效会话轮询永久挂起 — **已修复**

- **严重程度：** 中
- **接口：** `GET /api/chat/poll?session_id=<无效ID>`
- **预期：** 立即返回超时或错误响应
- **原因：** Pub/Sub 监听循环无外层超时保护
- **修复：** 新增 `_wait_for_response()` 函数，外层 `asyncio.wait_for` 硬超时保护
- **状态：** 已验证修复

### BUG-2：star-conn AB 端口缺少监控端点

- **严重程度：** 低（可能是设计如此）
- **接口：** `GET /api/monitor/customer-service/stats` (8081 端口)
- **预期：** 监控统计 JSON
- **实际：** 404 Not Found
- **说明：** 8081 端口为坐席后台，可能需要确认正确的监控端点路径

---

## 修复记录

| 修复项 | 修改前 | 修改后 | 验证结果 |
|--------|--------|--------|----------|
| 空消息校验 | 空消息被接受(200) | 返回 422 "消息内容不能为空" | ✅ |
| /notify 状态码 | 200 OK | 202 Accepted | ✅ |
| poll 挂起 | 无效会话永久阻塞 | 5s 超时返回 | ✅ |

## 观察与建议

1. **意图分类效果良好：** bill_query 置信度 0.85，transfer_agent 置信度 0.95
2. **转人工标记正确：** is_transfer 标志、transfer_reason、transfer_agent 意图均正确
3. **Streams 全链路通畅：** XADD → XREADGROUP → Agent 处理 → SETEX → PUBLISH → 轮询取回
4. **监控指标就绪：** 健康检查返回 PEL 深度、Stream 长度、活跃 Worker、Semaphore 利用率
5. **空消息校验已修复：** /chat/send 拒绝空白内容，返回 422
6. **HTTP 状态码已修复：** /notify 返回 202 Accepted
