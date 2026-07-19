# SmartCS 智能客服平台 API 测试案例

> **版本**: v1.0
> **日期**: 2026-05-11
> **测试类型**: API 契约/集成测试（非用户验收测试）
> **测试范围**: Bot 自助服务 (:8000) + 坐席辅助服务 (:8001) 全业务链路
> **测试环境**: 需启动 Redis、ES、Milvus、MinIO、PostgreSQL、star-connection 服务
> **测试方式**: curl / HTTP 客户端直接调用 REST API 和 WebSocket

---

## 一、测试模块总览

| 编号 | 模块 | 测试案例数 | 优先级 |
|------|------|-----------|--------|
| UAT-01 | 机器人对话核心流程 | 14 | P0 |
| UAT-02 | 意图分类准确性 | 10 | P0 |
| UAT-03 | 知识库检索 | 8 | P0 |
| UAT-04 | 文档入库 | 6 | P1 |
| UAT-05 | 转人工全链路 | 10 | P0 |
| UAT-06 | 坐席辅助 WebSocket | 8 | P0 |
| UAT-07 | 辅助分析推送 | 10 | P0 |
| UAT-08 | 风控与合规 | 10 | P0 |
| UAT-09 | 营销推荐与压制 | 8 | P1 |
| UAT-10 | 隐式反馈闭环 | 8 | P1 |
| UAT-11 | 会话状态管理 | 6 | P1 |
| UAT-12 | 降级与熔断 | 8 | P1 |
| UAT-13 | 安全与隐私 | 6 | P0 |
| UAT-14 | 边界与异常 | 8 | P2 |

**总计: 116 个测试案例**

---

## 二、通用说明

### 2.1 请求约定

- Bot 服务基址: `http://localhost:8000`
- Assist 服务基址: `http://localhost:8001`
- 所有 API 路径以 `/api` 开头
- Content-Type: `application/json`（除文档上传外）
- WebSocket 连接: `ws://localhost:8001/api/ws/{session_id}`

### 2.2 统一错误响应格式

```json
{
  "error": {
    "code": 2001,
    "message": "意图无法识别",
    "type": "IntentUnrecognizedError"
  }
}
```

### 2.3 会话阶段生命周期

```
BOT ──(触发转人工)──> HANDOFF ──(star-connection回调)──> ASSIST ──(通话结束)──> ENDED
```

---

## UAT-01: 机器人对话核心流程

### 前置条件
- 所有中间件已启动 (Redis, ES, Milvus, PostgreSQL)
- LLM 服务可用 (Ollama Qwen2.5-7B)
- 知识库已导入基础文档

| 编号 | 测试场景 | 操作步骤 | 预期结果 | 优先级 |
|------|----------|----------|----------|--------|
| 01-01 | 新会话首条消息 | 1. POST `/api/chat/send` body: `{"message": "你好", "channel": "web"}`<br>2. 记录返回的 `session_id`<br>3. GET `/api/chat/poll?session_id={sid}&timeout=25` | 1. send 返回 `accepted=true`，生成 `session_id` 和 `message_id`<br>2. poll 返回 `has_message=true`，`reply` 非空，`intent="chitchat"` | P0 |
| 01-02 | 带会话ID连续对话 | 1. POST `/api/chat/send` body: `{"message": "查询账单", "session_id": "{sid}"}`<br>2. GET `/api/chat/poll?session_id={sid}` | 1. send 返回相同 `session_id`<br>2. poll 返回账单相关回复，`intent="bill_query"` | P0 |
| 01-03 | 指定渠道 | 1. POST `/api/chat/send` body: `{"message": "积分查询", "channel": "app"}` | 返回 `accepted=true`，渠道正确记录 | P1 |
| 01-04 | 指定客户ID | 1. POST `/api/chat/send` body: `{"message": "查询额度", "customer_id": "CUST001", "channel": "wechat"}` | 返回 `accepted=true`，客户ID正确关联到会话 | P1 |
| 01-05 | Poll超时无消息 | 1. GET `/api/chat/poll?session_id=不存在的sid&timeout=1` | 返回 `has_message=false`，`reply=""` | P0 |
| 01-06 | 连续多轮对话 | 1. 依次发送: "你好" → "账单多少" → "怎么还款" → "谢谢"<br>2. 每次 poll 获取回复后发下一条 | 4轮对话均正常响应，意图依次为 chitchat → bill_query → faq/repayment → chitchat | P0 |
| 01-07 | 空消息 | 1. POST `/api/chat/send` body: `{"message": ""}` | 返回 HTTP 422，参数校验失败 | P0 |
| 01-08 | 超长消息 | 1. POST `/api/chat/send` body: `{"message": "测试"*5000}` (约10000字) | 请求被接受处理，回复正常（不崩溃） | P2 |
| 01-09 | 特殊字符消息 | 1. POST `/api/chat/send` body: `{"message": "<script>alert(1)</script>"}` | 不执行XSS，回复正常处理 | P0 |
| 01-10 | 并发同一会话 | 1. 同时发2条消息到同一 session_id<br>2. 分别 poll 获取结果 | 两条均正常处理，不出现数据混乱 | P1 |
| 01-11 | 无效渠道值 | 1. POST `/api/chat/send` body: `{"message": "测试", "channel": "invalid"}` | 返回 HTTP 422，channel 枚举校验失败 | P0 |
| 01-12 | 健康检查 | 1. GET `/api/health` | 返回 `{"status": "healthy", "service": "bot"}` | P0 |
| 01-13 | Poll参数边界 | 1. GET `/api/chat/poll?session_id=s1&timeout=0`<br>2. GET `/api/chat/poll?session_id=s1&timeout=61` | 两者均返回 HTTP 422（timeout 范围 1-60） | P1 |
| 01-14 | 会话自动过期 | 1. 创建会话后等待 TTL 过期 (默认30分钟)<br>2. 使用该 session_id 发送消息 | 1. 过期后原会话数据清除<br>2. 重新发送可创建新会话或恢复 | P2 |

---

## UAT-02: 意图分类准确性

### 前置条件
- 分类器已初始化，规则库已加载
- LLM 服务可用

| 编号 | 测试场景 | 操作步骤 | 预期结果 | 优先级 |
|------|----------|----------|----------|--------|
| 02-01 | 账单查询 - 快速路 | 1. 发送: "我上个月的账单是多少" | `intent="bill_query"`，`confidence>=0.7`，`source` 含 "rule" | P0 |
| 02-02 | 交易查询 - 快速路 | 1. 发送: "查一下最近交易记录" | `intent="transaction_query"`，`confidence>=0.7` | P0 |
| 02-03 | 额度查询 - 快速路 | 1. 发送: "我的信用卡额度是多少" | `intent="limit_query"`，`confidence>=0.7` | P0 |
| 02-04 | 分期咨询 - 快速路 | 1. 发送: "分期手续费怎么算" | `intent="installment_inquiry"`，`confidence>=0.7` | P0 |
| 02-05 | 积分查询 - 快速路 | 1. 发送: "查积分" | `intent="reward_query"`，`confidence>=0.7` | P0 |
| 02-06 | 挂失 - 快速路 | 1. 发送: "我的卡丢了要挂失" | `intent="card_loss"`，`confidence>=0.7` | P0 |
| 02-07 | 投诉 - 快速路 | 1. 发送: "我要投诉" | `intent="complaint"`，`confidence>=0.7` | P0 |
| 02-08 | 转人工 - 快速路 | 1. 发送: "转人工客服" | `intent="transfer_agent"`，`confidence>=0.7` | P0 |
| 02-09 | 模糊意图 - 慢路 | 1. 发送: "我这个月的还款日想改一下" | LLM分类被触发，返回合理意图（faq 或 installment_inquiry），`confidence>0.3` | P0 |
| 02-10 | 无法识别 - 降级 | 1. 发送: "asdfghjkl随机字符串" | `confidence<0.3`，仍返回回复（不崩溃），`source` 含 "fallback" | P1 |

---

## UAT-03: 知识库检索

### 前置条件
- ES + Milvus 已启动且索引已创建
- 已导入测试文档（至少包含: FAQ、费率说明、还款指南）

| 编号 | 测试场景 | 操作步骤 | 预期结果 | 优先级 |
|------|----------|----------|----------|--------|
| 03-01 | 混合检索默认 | 1. POST `/api/kb/retrieve` body: `{"query": "年费怎么收", "top_k": 5}` | 返回 `results` 非空，`total_candidates>0`，每条含 `chunk_id, content, score, source_doc` | P0 |
| 03-02 | BM25仅检索 | 1. POST `/api/kb/retrieve` body: `{"query": "年费怎么收", "search_type": "bm25_only"}` | 返回结果，仅走BM25通道 | P1 |
| 03-03 | 向量仅检索 | 1. POST `/api/kb/retrieve` body: `{"query": "年费怎么收", "search_type": "vector_only"}` | 返回结果，仅走向量通道 | P1 |
| 03-04 | 带过滤条件 | 1. POST `/api/kb/retrieve` body: `{"query": "年费", "filters": {"category": "ANNUAL_FEE"}}` | 返回结果全部匹配 category 过滤条件 | P1 |
| 03-05 | Top-K限制 | 1. POST `/api/kb/retrieve` body: `{"query": "信用卡", "top_k": 2}` | 返回结果数 <= 2 | P0 |
| 03-06 | 无关查询 | 1. POST `/api/kb/retrieve` body: `{"query": "今天天气怎么样"}` | 返回空结果或低分结果（`score < confidence_threshold`） | P1 |
| 03-07 | 重排序开关 | 1. POST `/api/kb/retrieve` body: `{"query": "分期利率", "rerank": true}`<br>2. 同查询 `"rerank": false` 对比 | 重排序后结果顺序可能不同，高分结果更相关 | P2 |
| 03-08 | 缺少query参数 | 1. POST `/api/kb/retrieve` body: `{"top_k": 5}` | 返回 HTTP 422 | P0 |

---

## UAT-04: 文档入库

### 前置条件
- MinIO 已启动且 bucket 已创建
- PostgreSQL 已启动
- ES + Milvus 索引已初始化

| 编号 | 测试场景 | 操作步骤 | 预期结果 | 优先级 |
|------|----------|----------|----------|--------|
| 04-01 | PDF文档入库 | 1. POST `/api/kb/documents` (multipart)<br>file=faq.pdf, category="FAQ", doc_type="policy" | 返回 `doc_id` 非空，`status="COMPLETED"`，`chunk_count>0` | P0 |
| 04-02 | Markdown文档入库 | 1. POST `/api/kb/documents`<br>file=guide.md, category="FAQ", doc_type="guide" | 返回 `doc_id` 非空，`status="COMPLETED"`，`chunk_count>0` | P1 |
| 04-03 | 不支持格式 | 1. POST `/api/kb/documents`<br>file=test.exe, category="FAQ", doc_type="test" | 返回 HTTP 400，`error.code=2010` | P0 |
| 04-04 | 带元数据入库 | 1. POST `/api/kb/documents`<br>file=card.pdf, category="FEE", doc_type="rate", card_type="platinum", customer_tier="gold", effective_date="2026-01-01" | 入库成功，检索时可按 card_type/customer_tier 过滤到该文档 | P1 |
| 04-05 | 空文件 | 1. POST `/api/kb/documents`<br>file=empty.txt (0字节), category="FAQ", doc_type="test" | 入库失败或返回 chunk_count=0 | P2 |
| 04-06 | 大文件 | 1. POST `/api/kb/documents`<br>file=large.pdf (>10MB) | 处理完成（可能耗时较长），不崩溃 | P2 |

---

## UAT-05: 转人工全链路

### 前置条件
- star-connection Java 服务已启动
- Bot + Assist 服务均可用
- 知识库已初始化

| 编号 | 测试场景 | 操作步骤 | 预期结果 | 优先级 |
|------|----------|----------|----------|--------|
| 05-01 | L1关键词触发 | 1. 发送: "转人工" | `is_transfer=true`，`transfer_reason` 非空，`transfer_url` 非空 | P0 |
| 05-02 | L1敏感词触发 | 1. 发送含敏感词的消息（如: "套现"） | `is_transfer=true`，触发转人工 | P0 |
| 05-03 | L2投诉意图 | 1. 发送: "我要投诉你们服务太差了" | `intent="complaint"`，`is_transfer=true` | P0 |
| 05-04 | L2负面情绪+高置信 | 1. 连续发送负面消息触发 angry 情绪<br>2. 发送: "你们银行怎么这样" | 当情绪为 angry 且 confidence>0.8 时，`is_transfer=true` | P1 |
| 05-05 | L3累计低置信 | 1. 连续发送3条模糊消息（如: "嗯"、"那个"、"就是"） | 低置信累计达3轮后，`is_transfer=true` | P1 |
| 05-06 | 挂失业务自动转人工 | 1. 发送: "我的信用卡丢了要挂失" | `intent="card_loss"`，`is_transfer=true`，`transfer_reason` 含 "挂失业务" | P0 |
| 05-07 | 转人工后poll含transfer_url | 1. 触发转人工<br>2. GET `/api/chat/poll` | poll 返回 `is_transfer=true`，`transfer_url` 非空（指向 star-connection） | P0 |
| 05-08 | 转人工时star-connection不可用 | 1. 停止 star-connection<br>2. 发送: "转人工" | `is_transfer=true`，`transfer_reason` 含 "人工客服系统暂不可用" | P1 |
| 05-09 | 会话阶段流转 BOT→HANDOFF | 1. 触发转人工<br>2. 检查 Redis 中会话状态 | 会话 `current_phase` 变为 "handoff" | P1 |
| 05-10 | 会话阶段流转 HANDOFF→ASSIST | 1. 触发转人工后<br>2. star-connection 回调 POST `/api/session/update` body: `{"session_id": "{sid}", "phase": "ASSIST", "agent_id": "agent-001"}` | 返回 `{"status": "ok"}`，会话阶段变为 "assist" | P0 |

---

## UAT-06: 坐席辅助 WebSocket

### 前置条件
- Assist 服务已启动
- session_manager + orchestrator 已初始化

| 编号 | 测试场景 | 操作步骤 | 预期结果 | 优先级 |
|------|----------|----------|----------|--------|
| 06-01 | 建立WS连接 | 1. 连接 `ws://localhost:8001/api/ws/session-ws-001` | 收到 `{"type": "assist_ready", "session_id": "session-ws-001", "message": "坐席辅助服务就绪"}` | P0 |
| 06-02 | 心跳检测 | 1. 建立WS连接<br>2. 等待15秒 | 收到 `{"type": "heartbeat"}` 消息 | P1 |
| 06-03 | 文本ping/pong | 1. 建立WS连接<br>2. 发送文本 `"ping"` | 收到文本 `"pong"` | P1 |
| 06-04 | JSON ping/pong | 1. 建立WS连接<br>2. 发送 `{"type": "ping"}` | 收到 `{"type": "pong"}` | P1 |
| 06-05 | 发送客户消息 | 1. 建立WS连接<br>2. 发送 `{"type": "customer_message", "message": "查询账单"}` | 收到 `{"type": "assist_push", ...}`，`payload` 含 scripts/knowledge/alerts | P0 |
| 06-06 | 无效JSON | 1. 建立WS连接<br>2. 发送 `not a json` | 收到 `{"type": "error", "message": "无效的 JSON"}` | P1 |
| 06-07 | 服务未就绪 | 1. 停止 orchestrator 或 session_manager<br>2. 尝试建立WS连接 | 收到 `{"type": "error", "message": "服务未就绪"}`，连接被关闭 | P0 |
| 06-08 | 断连后重连 | 1. 建立WS连接<br>2. 断开<br>3. 重新连接同一 session_id | 重连成功，收到 assist_ready 消息，连接池更新 | P1 |

---

## UAT-07: 辅助分析推送

### 前置条件
- Assist 服务已启动
- 坐席已通过 WS 连接

| 编号 | 测试场景 | 操作步骤 | 预期结果 | 优先级 |
|------|----------|----------|----------|--------|
| 07-01 | Analyze基本推送 | 1. 建立WS连接 (session_id=analyze-001)<br>2. POST `/api/analyze` body: `{"session_id": "analyze-001", "message": "查询账单"}` | WS 收到推送，`type="assist_push"`，payload 含 `primary_card`, `fusion_type` | P0 |
| 07-02 | Analyze无WS连接 | 1. POST `/api/analyze` body: `{"session_id": "no-ws-session", "message": "查询"}` | HTTP 返回 `{"status": "ok", ...}`，不报错（日志记 warning） | P0 |
| 07-03 | 分类器集成 | 1. POST `/api/analyze` body: `{"session_id": "cls-001", "message": "上个月账单多少"}` | 返回 `intent="bill_query"`，`confidence>0` | P0 |
| 07-04 | 分类器超时降级 | 1. 模拟分类器响应慢 (>3秒)<br>2. POST `/api/analyze` | 返回 `intent="faq"`（默认值），HTTP仍为200 | P1 |
| 07-05 | 无Temporal降级到Orchestrator | 1. Temporal 不可用时<br>2. POST `/api/analyze` | 降级到 AssistOrchestrator，WS 推送仍有结果 | P1 |
| 07-06 | 无Temporal无Orchestrator | 1. Temporal + Orchestrator 均不可用<br>2. POST `/api/analyze` | 返回空 payload，HTTP仍为200，不崩溃 | P1 |
| 07-07 | 缺少session_id | 1. POST `/api/analyze` body: `{"message": "查询"}` | 返回 HTTP 422 | P0 |
| 07-08 | 缺少message | 1. POST `/api/analyze` body: `{"session_id": "s1"}` | 返回 HTTP 422 | P0 |
| 07-09 | 多次推送不重复 | 1. 连续 POST `/api/analyze` 3次<br>2. 观察WS推送 | 每次推送内容独立，不重复推送旧结果 | P1 |
| 07-10 | 辅助服务健康检查 | 1. GET `/api/health` | 返回 `{"status": "healthy", "service": "assist"}` | P0 |

---

## UAT-08: 风控与合规

### 前置条件
- Assist 服务已启动
- AlertEngine 规则已加载

| 编号 | 测试场景 | 操作步骤 | 预期结果 | 优先级 |
|------|----------|----------|----------|--------|
| 08-01 | 违规承诺-风控BLOCK | 1. 发送含 "套现包过" 的消息到 analyze | 仲裁结果 `fusion_type="risk_blocked"`，`primary_card.type="risk_block"`，营销不展示 | P0 |
| 08-02 | 过度承诺-风控WARN | 1. 发送含 "保证批" 的消息到 analyze | 仲裁结果 `fusion_type="service_risk_warn"`，`risk_badge` 非空，营销降级为 `marketing_small` | P0 |
| 08-03 | 正常消息-风控PASS | 1. 发送普通查询消息到 analyze | 仲裁结果 `fusion_type="service_only"` 或 `"service_marketing"`，`risk_badge` 为 null | P0 |
| 08-04 | 不文明用语 | 1. 发送含脏话的消息 | 触发合规告警 `level="warning"`，`category="compliance"` | P0 |
| 08-05 | 敏感卡片信息 | 1. 发送含 "CVV" 或 "密码" 的消息 | 触发 info 级别告警 `category="compliance"` | P1 |
| 08-06 | 合规短语过滤 | 1. 发送含 "保证收益" 的话术 | 话术中 "保证收益" 被替换为 "[已过滤]" | P0 |
| 08-07 | 多条合规规则同时命中 | 1. 发送含 "套现" + "保证批" + 不文明用语的消息 | 触发多条告警，按优先级排序，critical 优先 | P1 |
| 08-08 | 客户情绪ANGRY | 1. 检测到客户情绪为 angry | 触发 critical 级别情绪告警 | P0 |
| 08-09 | 连续负面情绪趋势 | 1. 连续3轮检测到 negative/angry 情绪 | 触发 critical 级别趋势告警，建议转主管 | P1 |
| 08-10 | 正常情绪无告警 | 1. 检测到客户情绪为 positive/neutral | 无情绪类告警 | P0 |

---

## UAT-09: 营销推荐与压制

### 前置条件
- Assist 服务 + Temporal 已启动
- 营销评估器 (D2) 可用

| 编号 | 测试场景 | 操作步骤 | 预期结果 | 优先级 |
|------|----------|----------|----------|--------|
| 09-01 | 正常营销展示 | 1. 客户情绪积极，发送非业务消息<br>2. D1+D2 同时激活 | `fusion_type="service_marketing"`，`marketing_slot.type="marketing_standard"` | P0 |
| 09-02 | 服务压制营销 | 1. D1(服务) + D2(营销) 同时激活<br>2. 策略压制D2 | D2 被压制2轮，`marketing_slot` 为 null，状态写入 `suppress_flag=True` | P0 |
| 09-03 | 压制过期恢复 | 1. 压制2轮后<br>2. 检查 suppress_flag | 2轮后 `suppress_flag` 恢复为 False（通过 suppress_force_clear） | P1 |
| 09-04 | 风控BLOCK压制营销 | 1. 风控返回 BLOCK<br>2. 检查营销展示 | E2 不执行，`marketing_slot` 为 null | P0 |
| 09-05 | 风控WARN降级营销 | 1. 风控返回 WARN<br>2. 营销仍可展示 | `marketing_slot.type="marketing_small"`（降级小卡片） | P0 |
| 09-06 | 情绪过期不触发营销 | 1. 客户5分钟前情绪积极但当前无情绪<br>2. 检查D2激活 | D2 不激活（情绪衰减后 score < 0.3），无营销 | P1 |
| 09-07 | 新鲜情绪触发营销 | 1. 客户当前情绪积极 (score=0.9)<br>2. D2 评估 | D2 激活，cooldown_remaining=5 | P1 |
| 09-08 | 营销冷却期 | 1. D2 刚激活过 (cooldown_remaining>0)<br>2. 下一次评估 | D2 不激活，处于冷却期 | P1 |

---

## UAT-10: 隐式反馈闭环

### 前置条件
- Assist 服务已启动
- StateManager 可用

| 编号 | 测试场景 | 操作步骤 | 预期结果 | 优先级 |
|------|----------|----------|----------|--------|
| 10-01 | accept反馈 | 1. POST `/api/feedback` body: `{"session_id": "s1", "agent_id": "a1", "action": "accept"}` | 返回 `{"status": "ok", "action": "accept", "confidence": 1.0, "delayed_commit": true}` | P0 |
| 10-02 | modify反馈 | 1. POST `/api/feedback` body: `{"session_id": "s2", "agent_id": "a1", "action": "modify", "modify_fields": ["script_content"]}` | 返回 `confidence=0.5`，缓冲区包含 modify_fields | P0 |
| 10-03 | partial_accept反馈 | 1. POST `/api/feedback` body: `{"session_id": "s3", "agent_id": "a1", "action": "partial_accept"}` | 返回 `confidence=0.3` | P1 |
| 10-04 | reject反馈(默认) | 1. POST `/api/feedback` body: `{"session_id": "s4", "agent_id": "a1"}` | 返回 `action="reject"`，`confidence=0.0` | P0 |
| 10-05 | 3秒内撤销 | 1. POST `/api/feedback` (accept)<br>2. 立即 POST `/api/feedback/undo` body: `{"session_id": "s5", "agent_id": "a1"}` | 撤销成功: `{"status": "ok", "undone": true}`，反馈不提交到Redis | P0 |
| 10-06 | 撤销不存在的反馈 | 1. POST `/api/feedback/undo` body: `{"session_id": "none", "agent_id": "a1"}` | 返回 `{"status": "ok", "undone": false, "reason": "not_buffered"}` | P1 |
| 10-07 | 缓冲区覆盖 | 1. POST `/api/feedback` (reject)<br>2. POST `/api/feedback` (accept)，同一 session+agent | 缓冲区被覆盖，最终以 accept 为准 | P1 |
| 10-08 | 反馈参数校验 | 1. 缺少 session_id<br>2. 缺少 agent_id<br>3. 无效 action | 均返回 HTTP 422 | P0 |

---

## UAT-11: 会话状态管理

### 前置条件
- Redis 已启动
- SessionManager + StateManager 已初始化

| 编号 | 测试场景 | 操作步骤 | 预期结果 | 优先级 |
|------|----------|----------|----------|--------|
| 11-01 | 会话创建 | 1. POST `/api/chat/send` (无 session_id) | 自动生成 session_id，Redis 中可查到会话数据 | P0 |
| 11-02 | 会话阶段更新 ASSIST | 1. POST `/api/session/update` body: `{"session_id": "s1", "phase": "ASSIST", "agent_id": "a1"}` | 返回 `{"status": "ok"}`，会话阶段变为 assist | P0 |
| 11-03 | 会话阶段更新 ENDED | 1. POST `/api/session/update` body: `{"session_id": "s1", "phase": "ENDED"}` | 返回 `{"status": "ok"}`，会话阶段变为 ended | P0 |
| 11-04 | 无效阶段值 | 1. POST `/api/session/update` body: `{"session_id": "s1", "phase": "INVALID"}` | 返回 HTTP 400，`error.code=2001` | P0 |
| 11-05 | 无SessionManager | 1. 停止 SessionManager<br>2. POST `/api/session/update` | 返回 HTTP 500，`error.code=5001` | P1 |
| 11-06 | CAS状态冲突 | 1. 两个并发写操作使用相同 expected_version | 至少一个返回冲突错误或重试成功 | P2 |

---

## UAT-12: 降级与熔断

### 前置条件
- 服务已启动
- 可模拟各中间件不可用

| 编号 | 测试场景 | 操作步骤 | 预期结果 | 优先级 |
|------|----------|----------|----------|--------|
| 12-01 | LLM不可用降级 | 1. 停止 Ollama LLM<br>2. 发送查询消息 | Bot 降级为模板回复或检索摘要，`source` 为 "retrieval" 或 "template" | P0 |
| 12-02 | ES不可用-仅向量检索 | 1. 停止 ES<br>2. POST `/api/kb/retrieve` | 自动降级为 vector_only 检索 | P1 |
| 12-03 | Milvus不可用-仅BM25 | 1. 停止 Milvus<br>2. POST `/api/kb/retrieve` | 自动降级为 bm25_only 检索 | P1 |
| 12-04 | ES+Milvus均不可用 | 1. 同时停止 ES 和 Milvus<br>2. 发送查询消息 | 检索结果为空，Bot 使用模板回复，`source="template"` | P1 |
| 12-05 | Embedding服务不可用 | 1. 停止 Embedding 服务<br>2. 发送查询消息 | 自动降级为 BM25 检索，不崩溃 | P1 |
| 12-06 | E1执行器降级 | 1. 模拟 E1 超时<br>2. 检查辅助推送 | E1 返回 `degraded=true`，仲裁仍能输出 (safe_fallback) | P1 |
| 12-07 | E3风控降级 | 1. 模拟 E3 不可用<br>2. 检查辅助推送 | E3 返回 `pass_with_audit_flag`，`risk_pending_audit=true` | P1 |
| 12-08 | 编排全局超时 | 1. 模拟全链路耗时超5秒<br>2. 检查推送结果 | 返回 `fusion_type="timeout_partial"`，部分结果可用 | P1 |

---

## UAT-13: 安全与隐私

### 前置条件
- Assist 服务已启动
- 仲裁器 PII 规则已加载

| 编号 | 测试场景 | 操作步骤 | 预期结果 | 优先级 |
|------|----------|----------|----------|--------|
| 13-01 | 手机号脱敏 | 1. 话术中含 "13800138000"<br>2. 检查仲裁输出 | 输出中手机号被替换为 "[PHONE]" | P0 |
| 13-02 | 银行卡号脱敏 | 1. 话术中含 "6222021234567890"<br>2. 检查仲裁输出 | 输出中卡号被替换为 "[BANKCARD]" | P0 |
| 13-03 | 身份证号脱敏 | 1. 话术中含18位身份证号<br>2. 检查仲裁输出 | 输出中身份证号被替换为 "[IDCARD]" | P0 |
| 13-04 | 客户姓名脱敏 | 1. 话术中含 "客户张三的"<br>2. 检查仲裁输出 | 输出中姓名被替换为 "客户[NAME]的" | P0 |
| 13-05 | PII脱敏覆盖所有卡片 | 1. 发送含 PII 的消息<br>2. 检查 primary_card、risk_badge、marketing_slot | 所有卡片中均不含原始 PII | P0 |
| 13-06 | 短数字不误脱敏 | 1. 话术中含 "3位数字如 123"<br>2. 检查仲裁输出 | 短数字不被误替换 | P1 |

---

## UAT-14: 边界与异常

| 编号 | 测试场景 | 操作步骤 | 预期结果 | 优先级 |
|------|----------|----------|----------|--------|
| 14-01 | Redis不可用 | 1. 停止 Redis<br>2. 发送聊天消息 | Bot 返回 503 或降级响应，不崩溃 | P0 |
| 14-02 | 数据库不可用 | 1. 停止 PostgreSQL<br>2. 尝试文档入库 | 返回适当错误码，不崩溃 | P1 |
| 14-03 | 并发高负载 | 1. 模拟50个并发用户同时发送消息 | 系统不崩溃，所有请求得到响应（可能降级） | P2 |
| 14-04 | WebSocket长时间连接 | 1. 建立WS连接后保持30分钟 | 心跳正常，连接不断 | P2 |
| 14-05 | 同一session多个WS连接 | 1. 同一 session_id 建立两个WS连接 | 后者覆盖前者，推送只发给最新连接 | P1 |
| 14-06 | 无效的session/update | 1. POST `/api/session/update` body: `{}` | 返回 HTTP 422，参数校验失败 | P0 |
| 14-07 | 跨服务会话一致性 | 1. Bot创建会话 → 转人工 → Assist阶段 → 结束 | 全流程会话状态一致，阶段正确流转 | P1 |
| 14-08 | Prometheus指标 | 1. GET `/metrics` | 返回 Prometheus 格式指标数据 | P2 |

---

## 三、端到端业务场景测试

### 场景 A: 客户咨询→自助解决

```
客户: "你好"
  → Bot: chitchat 回复
客户: "年费怎么收"
  → Bot: RAG检索返回年费政策 (intent=bill_query, source=rag)
客户: "好的谢谢"
  → Bot: 结束对话 (intent=chitchat)
```

**验证点**: 全程不触发转人工，source 从 chitchat→rag→chitchat 正确流转

### 场景 B: 客户咨询→转人工→坐席辅助

```
客户: "账单有疑问"
  → Bot: RAG回复 (intent=bill_query)
客户: "不对，我要转人工"
  → Bot: 转人工 (intent=transfer_agent, is_transfer=true)
  → star-connection 分配坐席
  → 坐席 WS 连接建立
  → POST /api/session/update phase=ASSIST
客户(通过star-connection): "上个月的退款怎么还没到"
  → POST /api/analyze
  → 坐席WS收到 assist_push (primary_card + fusion_type)
坐席: 采纳话术发送
  → POST /api/feedback action=accept
通话结束:
  → POST /api/session/update phase=ENDED
```

**验证点**: 会话阶段 BOT→HANDOFF→ASSIST→ENDED 完整流转，辅助推送正常，反馈采集正常

### 场景 C: 风控拦截场景

```
客户(通过star-connection): "你们能帮我套现吗"
  → POST /api/analyze
  → AlertEngine 命中 R-COMP-001 (critical)
  → E3 返回 risk_action=BLOCK
  → 仲裁结果 fusion_type=risk_blocked
  → 坐席WS收到: primary_card.type=risk_block, marketing_slot=null
```

**验证点**: 风控BLOCK时营销不展示，坐席看到风控拦截卡片

### 场景 D: 降级全链路

```
1. 停止 LLM 服务
2. 客户: "信用卡额度多少"
  → Bot: 降级为模板回复 (source=template)
3. 客户: "转人工"
  → 转人工成功
4. 停止 Temporal
5. 坐席辅助仍可用 (降级到 AssistOrchestrator)
```

**验证点**: LLM 降级不阻塞对话，Temporal 降级不阻塞辅助

---

## 四、验收标准

| 指标 | 标准 |
|------|------|
| P0 案例通过率 | 100% |
| P1 案例通过率 | >= 90% |
| P2 案例通过率 | >= 70% |
| 全链路场景通过 | 4/4 |
| 无 P0 级 Bug | 0 个 |
| 无数据丢失/泄漏 | 0 个 |

---

## 五、测试数据准备

### 5.1 知识库文档

| 文档 | 类型 | 内容 |
|------|------|------|
| faq_v1.md | FAQ | 信用卡常见问题20条 |
| fee_policy.pdf | FEE | 年费/利息/滞纳金政策 |
| reward_guide.md | POINTS | 积分规则与兑换指南 |
| annual_fee.html | ANNUAL_FEE | 年费减免条件 |

### 5.2 敏感词配置

```
config/safety/sensitive_words.txt:
套现
洗钱
反洗钱
内部渠道
```

### 5.3 测试账号

| 角色 | ID | 用途 |
|------|-----|------|
| 客户 | CUST001 | 普通客户 |
| 客户 | CUST002 | VIP客户 (vip_level=gold) |
| 坐席 | agent-001 | 正常坐席 |
