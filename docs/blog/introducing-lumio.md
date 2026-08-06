# 灵智（Lumio）：银行信用卡智能客服的工程化设计

> 一句话：**灵智（Lumio）** 是一个可私有化部署的银行信用卡智能客服参考实现 —— Bot 自助问答 + AI 坐席辅助双引擎，RAG 检索增强、意图识别、合规过滤、熔断降级、实时监控全配齐，`make demo` 一条命令即可体验。
>
> 仓库：https://github.com/slowleelab/lumio ｜ License：Apache 2.0

![demo](../assets/demo.gif)

## 为什么做这个

市面上智能客服方案不少，但要么是绑死公有云的 SaaS，要么是只有 Demo 级 RAG 的玩具项目。金融场景有几个硬约束，是大多数开源项目覆盖不到的：

- **数据不出域**：客户对话、卡片信息必须私有化，LLM 也得能跑在本地（Ollama / vLLM）
- **合规可审计**：每句话要过敏感词/合规过滤，对话留痕满足银行 5-7 年审计要求
- **高可用不能靠运气**：LLM 挂了、向量库挂了，服务不能跟着挂 —— 要有完整的熔断 + 多级降级链
- **人机协同**：机器人搞不定的要平滑转人工，坐席通话中还要实时给话术建议

## 三层架构

```
┌─────────────────────────────────────────────┐
│  编排层 (FastAPI · agent/lumio/)             │
│  ┌──────────────┐   ┌──────────────────────┐ │
│  │ Bot 自助服务  │   │  AI 坐席辅助服务      │ │
│  │   :8000      │   │  :8001 (WebSocket)   │ │
│  │  + WS 流式    │   │                      │ │
│  └──────┬───────┘   └──────────┬───────────┘ │
├─────────┼──────────────────────┼──────────────┤
│  AI 能力 (HTTP 直连 Ollama/TEI · Higress 网关) │
│  意图分类 │ RAG 检索 │ LLM │ 安全过滤          │
│  + Java MCP Server :8090 (22 tools, mock)   │
├───────────────────────────────────────────────┤
│  数据层 (deploy/docker-compose.yml · lumio-*)│
│  PostgreSQL · Redis · ES(IK) · Milvus · MinIO│
│  Kafka · Prometheus · Grafana · Jaeger       │
│  Higress + Nacos (gateway profile, opt-in)   │
└─────────────────────────────────────────────┘
```

---

## 一、上下文工程：分层消息 + 预算强制分配 + 精排重排

### 1.1 分层消息结构（KV Cache 友好）

所有 LLM 调用走**分层消息构建器**，静态与动态内容物理分层，最大化前缀缓存命中：

```
[system L1: 静态角色+合规 (cache_control ephemeral)]     ← 100% 命中
[system L1.5: few-shot 案例 (cache_control ephemeral)]    ← 同意图缓存
[system L2: 客户画像 (cache_control ephemeral)]           ← 跨会话缓存
[system L3: 会话记忆+槽位]                                ← 动态 (限 400 tokens)
[history]                                                 ← 动态 (限 1500 + 压缩)
[user: <retrieved_context> RAG + 用户输入]                ← 动态 (限 1200 + 精排)
```

- **L1 静态锚点**：角色定义 + 合规规则，`[STATIC_PREFIX_v1]` 标记 + `cache_control: ephemeral`，永不变化，推理引擎（vLLM/Anthropic）自动缓存
- **L2 半稳态**：客户画像（VIP/卡种/风险偏好），同一客户跨会话命中
- **L3 动态**：会话记忆 + 槽位，每轮必变
- **RAG 物理隔离**：检索内容放 user message 的 `<retrieved_context>` 包裹内，与指令系统消息隔离（防注入 + 不污染缓存前缀）

`DegradationManager.generate_with_fallback` 支持 `messages` 直传完整分层数组，降级链（LLM → 检索摘要 → 模板 → 兜底）语义完整保留。

### 1.2 Token 预算：分层强制分配 + 汇总校验

按层分配并**在代码中强制消费**（不是文档约定）：

| 层 | 预算 | 机制 |
|---|---|---|
| 静态 system | 800 | 常量 |
| 半稳态客户画像 | 400 | `_build_session_memory` 超限截断 |
| RAG 检索 | 1200 | `_retrieve` 按 token 累加截断 |
| 历史 | 1500 | `_load_history` 预算裁剪 + 压缩 + 增量摘要 |
| 输出保留 | 1024 | `reserved_tokens` |

- 历史裁剪：从最近向前累加，**关键轮次**（投诉/承诺/转人工，15 个关键词）永不裁剪
- 被裁剪轮次触发**增量摘要**：`last_summarized_turn_id` 指针精确追踪，只摘要新增部分，摘要本身限 1500 tokens
- 超预算先走**选择性压缩**（`context_compressor`：分句 → 重要性打分 → 质量门 `min_quality_score` 校验，9 类金融保护模式强制保留：金额/日期/产品码等），压缩不达标才裁剪
- 汇总校验：Σ各层 ≤ context − reserved，超限 WARNING

### 1.3 RAG：混合检索 + 精排 + 首尾重排

- **BM25（ES + IK 分词）+ 向量（Milvus）双路召回，RRF 融合**，任一路挂了优雅降级成单路
- **Reranker 精排**（Ollama 生成式 / TEI 双 provider）+ 相关性阈值过滤
- **首尾重排**（LongLLMLingua `reorder_context="sort"` 实践）：相关性最高文档置首、次高置尾，对抗 lost-in-the-middle
- 检索缓存 key 含 `include_expired`/`rerank` 维度，过期文档结果不与默认请求互用
- RAG 内容进 LLM 前过 `ContentSanitizer`（A4 注入防护）

### 1.4 记忆体系

```
Core (常驻 system)    ← 摘要(限1500) + 画像(限400) + 意图栈(上限10) + 实体白名单
Working (近史)        ← 20 轮窗口, 1500 token 预算, 被裁部分转摘要
Archival (按需)       ← 90 天画像聚合: 每日离线可缓存(24h Redis), D0 衰减 0.95^days
```

- 意图栈上限 10（防无界膨胀破坏前缀缓存）
- 画像学习异步执行（不阻塞用户请求），带衰减时间戳（`*_updated_at`），超过 999 天强制降级
- 跨会话实体白名单 7 类（card_type/vip_level/risk_tolerance/city/occupation/age_range/product_interest），PII 类型（卡号/身份证/手机号）永不跨会话

---

## 二、消息可靠性与幂等

### 2.1 消息管道：at-least-once 语义

```
chat/send → Redis Stream (XADD) → per-session 队列 (串行) → Agent 处理 → response key + Pub/Sub 通知 → chat/poll 消费
                                                              ↕ XAUTOCLAIM 60s 重投 (挂死消息)
                                                              ↕ 死信队列 (超重试上限)
```

- **per-session 串行**：同一会话消息按序处理，无并发交错
- **XAUTOCLAIM 重投**：处理中断的消息 60s 后重新认领，at-least-once
- **死信队列**：超重试上限进 `lumio:chat:dead_letter`（admin 可查，已认证）

### 2.2 幂等：消息级 + 操作级双保险

- **消息级幂等**：每条消息处理完成写 `lumio:processed:{message_id}`（TTL 300s，覆盖重投窗口），重投递时跳过已处理的，`continue` 不杀 worker
- **操作级幂等**：敏感工具（挂失/调额/分期）确认执行以 `pending.tool_call_id` 为幂等键（SETNX + 24h TTL），重投递/CAS 失败都不会重复办理
- 确认状态机：pending（确认话术）→ executing（幂等键写入）→ done（清除）；`_clear_pending_action` CAS 重试 ×3

### 2.3 工具调用治理

- **执行侧白名单**：工具名必须存在于注册缓存，幻觉调用直接拒绝并回喂 "tool not found"
- **渐进式暴露**：仅按意图暴露相关工具子集（`MCP_INTENT_TOOL_MAP`）
- **配额**：per-customer per-tool 窗口计数（Lua 原子 INCR+EXPIRE），超限拒绝
- **重试**：网络类错误指数退避重试 ×3
- **结果治理**：4096 字节截断 + PII 脱敏 + 审计，防 prompt flooding

---

## 三、安全与合规

### 3.1 认证与授权

- 全部 chat/session/admin 端点强制 JWT 认证（`CurrentUser` 依赖），chat 通道默认 30/min 限流
- **session 归属校验**：JWT 声明 session_id 与请求不一致 → 403；customer 读取他人会话按 meta owner 二次校验
- **角色矩阵**：敏感词管理 / 死信队列 / 审批链（approve/reject/publish/archive）/ 规则热加载 / 业务统计 → `require_role("admin")`
- WS 通道握手鉴权（query param token，生产校验有效性）

### 3.2 注入防护（3 层）

- **User Input**：正则（20+ 模式）+ 角色混淆检测 + Guard LLM，入口 `chat/send` 拒绝
- **RAG 内容**：`sanitize_rag_content` 净化指令性语句，`<retrieved_context>` 包裹物理隔离，替换文本不暴露 pattern 名
- **Tool 返回**：`sanitize_tool_result` 字段级过滤 + 4096 截断 + PII 脱敏
- 日志侧：PII 脱敏覆盖 msg/args/traceback/extra 全通道

### 3.3 合规过滤（Milvus 向量通道）

`approval_status` / `is_current_version` 写入 Milvus 标量字段（schema + ingestion + output_fields 三处一致），检索后**严格过滤**——缺失字段视为不合规，不做默认放行。ES 侧 term 过滤 + 检索缓存维度隔离双保险。

### 3.4 GDPR 删除全链路

`POST /api/gdpr/delete`（本人/admin 可发起）→ 软删除（30 天观察）→ 立即清理活跃会话 → 后台 sweep worker 每小时消费调度 ZSET 执行硬删除：

- **Redis**：SCAN `lumio:session:*:meta` 解析 customer_id 匹配，删 meta/history/slot
- **PostgreSQL**：dialogue_log / decision_log / chat_message 三表
- **Milvus / ES**：按 customer_id 删除向量与文档

### 3.5 会话生命周期

`bot → agent → ended` 3 阶段 × 7 子状态（agent 下 queued/assigned/active/on_hold/reviewing），超时守卫存 Redis ZSET（member 编码 sub_phase，多实例语义正确），排队超时回退 BOT，TTL 恒大于最长超时（防审计落库缺口）。

---

## 四、可观测性与运维

### 4.1 指标（16+ 个，`lumio_*` 命名空间）

- **成本**：每次 LLM 调用上报真实 token/成本（`llm_token_usage_total` / `llm_cost_usd_total`），月度/租户预算熔断
- **性能**：请求延迟（低基数 path label，UUID/长数字段归一化为 `{param}`）、LLM 延迟、流式 TTFT
- **业务**：会话阶段耗时、降级级别、工具调用（含 quota_exceeded）、注入拦截、确认决策
- **质量保障**：dashboard 指标名与代码一致性由 `verify-observability` 脚本 + CI 双重校验

### 4.2 告警

`build_alert_rules` 在 lifespan 接线，每分钟扫描 LLM 错误率/预算超限指标 → P0/P1 分级告警（console/log handler，PagerDuty 等外部渠道可扩展），后台循环 task 持有引用防 GC。

### 4.3 降级链

```
LLM 生成 → 检索摘要拼接 → 预置话术模板 → 兜底文案
```

- 连续失败阈值 → DEGRADED（跳过 LLM 用检索摘要）；失败 ×3 → FALLBACK（跳过检索用模板）
- 健康探测：健康时 10s 间隔（防计费浪费 + 指标污染），故障时指数退避
- 健康检查覆盖 Redis/PG/ES/LLM/Embedding/Milvus 六项，live/ready 端点分离

### 4.4 决策可追溯（E2）

每条决策（意图分类/工具调用/检索/注入拦截/确认/压缩）双写：Redis（最近 100 条实时查询）+ PostgreSQL `decision_log` 表（alembic 迁移，3 复合索引支持客户查询/监管审计/GDPR 删除）。

---

## 五、工程化

- **测试**：716 条 pytest 通过（+5 跳过），覆盖消息管道/确认状态机/上下文预算/压缩质量门/会话超时/认证越权/配置校验；纯 mock 集成测试进 CI
- **CI**：lint（ruff）+ mypy（advisory 计数）+ 单测（覆盖率门禁 55%）+ 观测一致性校验 + Docker 构建 + e2e smoke（带 token）
- **部署**：生产环境凭据强制校验（JWT/LLM/MinIO/ES/Redis 缺一拒绝启动）；k8s manifest 显式 SERVICE env、secret 注入、terminationGracePeriod 90s（> LLM timeout）
- **初始化**：启动步骤全量展开无重复，全局 session factory / Redis 客户端 / GDPR sweep / 告警规则全部接线

## 5 分钟跑起来

```bash
git clone https://github.com/slowleelab/lumio.git && cd lumio
make demo
```

起来后直接问一句：

```bash
curl -X POST http://localhost:8000/api/chat/send \
  -H 'Content-Type: application/json' \
  -d '{"message":"信用卡年费怎么减免"}'
```

会得到一个带意图识别结果的回答。有 Ollama 走真实大模型；没有就自动降级，流程照样通。

## 技术栈速览

- **编排**：FastAPI + asyncio（无 LangGraph 依赖，规则路由 + 状态机）
- **检索**：Elasticsearch（IK 分词 + BM25）+ Milvus（向量）+ RRF 融合 + Reranker 精排
- **LLM**：OpenAI 兼容 API（Ollama / vLLM），Qwen 系模型，JSON 模式分类
- **工具**：Java Spring AI MCP Server，22 个信用卡工具（mock）
- **存储**：PostgreSQL 16 / Redis 7.2 / MinIO / Kafka
- **观测**：Prometheus / Grafana / Jaeger / 结构化 JSON 日志（PII 脱敏）

## 适合谁

- 金融/政企想做**私有化智能客服**，需要一个能落地的技术基座
- 想学习 **RAG + Agent 编排 + 熔断降级 + MCP 工具**在真实业务里怎么组合
- 团队要快速验证智能客服 PoC，不想从零搭中间件和降级策略

如果这个项目对你有帮助，欢迎 [Star](https://github.com/slowleelab/lumio) ⭐；问题去 [Discussions](https://github.com/slowleelab/lumio/discussions) 聊，Bug 走 [Issues](https://github.com/slowleelab/lumio/issues)，PR 从[贡献指南](../../CONTRIBUTING.md)开始。
