---
title: "附录 C: 常见问题排查"
part: "附录"
last_updated: "2026-08-05"
summary: "20+ 常见生产/开发问题排查手册: LLM 不可用 / 嵌入熔断 / ES 不可用 / Milvus 不可用 / Kafka 延迟 / 启动顺序 / JWT 错误 / 跨服务 trace 断裂 / 健康检查假阳性等."
tags: ["故障排查", "troubleshooting", "降级", "告警"]
---

# 附录 C: 常见问题排查

> 本附录汇总 Lumio 22 个最常见生产/开发问题, 按 6 大类组织. 每个问题给「症状 / 原因 / 排查 / 修复」4 步.

## C.1 LLM 链路故障

### C.1.1 LLM 502 错误

**症状**: Bot/Assist 回答 502, 客户端显示"系统繁忙".

**原因**:
- LLM 服务 (Ollama/OpenAI) 不可达
- LLM 熔断器 (LLMCircuitBreaker) 已 OPEN
- API key 失效

**排查**:
```bash
# 1. 检查 LLM 服务
curl http://localhost:11434/v1/models

# 2. 检查熔断器状态 (通过指标)
curl http://localhost:8000/metrics | grep lumio_llm_circuit_state

# 3. 检查 API key
echo $LLM_API_KEY
```

**修复**:
- 服务不可达 → `systemctl restart ollama` 或检查防火墙
- 熔断器 OPEN → 等待 30s 自动转 HALF_OPEN, 或手动 reset
- API key 失效 → 更新 `LLM_API_KEY` env, 重启服务

### C.1.2 LLM 响应慢 (>5s)

**症状**: P99 延迟 >5s, Grafana `LLMLatencyP99High` 告警.

**原因**:
- Ollama CPU 推理 (无 GPU)
- prompt 过长 (历史 + chunks 超过 4K token)
- LLM 服务过载

**排查**:
```bash
# 1. 看 Grafana llm_call_duration_seconds p99
# 2. 检查 prompt 长度
poetry run python -c "from lumio.shared.llm import LLMClient; print(LLMClient().max_tokens)"
```

**修复**:
- 启用 GPU 加速或换更小模型 (qwen2.5:0.5b)
- 减少 history 轮数 (默认 20, 改 10)
- 启用 LLM 缓存 (相同 query 命中)

### C.1.3 LLM 幻觉 (编造内容)

**症状**: 客户投诉"AI 回答了不存在的政策".

**原因**:
- RAG 检索召回空, LLM 自由发挥
- prompt 缺少"不要编造"约束

**排查**:
```bash
# 检查检索召回数
curl -X POST http://localhost:8000/api/kb/retrieve \
  -H "Content-Type: application/json" \
  -d '{"query": "信用卡年费", "top_k": 5}'
```

**修复**:
- 召回 0 → 检查文档审批流是否到 PUBLISHED
- prompt 加约束: "如果不知道, 请回答'请咨询人工坐席'"

## C.2 嵌入 / RAG 链路故障

### C.2.1 EmbeddingCircuitBreaker OPEN

**症状**: RAG 检索走 bm25_only, 召回率下降 50%.

**原因**:
- TEI 服务 (Text Embeddings Inference) 不可达
- 连续 3 次失败触发熔断

**排查**:
```bash
# 1. 检查 TEI 服务
curl http://localhost:8081/health
# 2. 看熔断器状态
curl http://localhost:8000/metrics | grep embedding_breaker
```

**修复**:
- TEI 不可达 → `systemctl restart tei`
- 自动恢复: 30s 后转 HALF_OPEN, 2 次成功关熔断

### C.2.2 RAG 召回空 (`retrieve` 返 `[]`)

**症状**: 客户问知识类问题, AI 答"请咨询人工".

**原因**:
- ES + Milvus 双挂 (罕见)
- 文档还未 PUBLISHED
- effective_date 还没到

**排查**:
```bash
# 1. 单独测 ES
curl http://localhost:9200/lumio_kb_chunks/_count
# 2. 单独测 Milvus
poetry run python -c "from pymilvus import connections; connections.connect(host='localhost', port='19530'); print(connections.list_connections())"
# 3. 查某文档状态
psql -U lumio -d lumio -c "SELECT id, title, approval_status FROM kb_document WHERE title LIKE '%年费%';"
```

**修复**:
- ES 挂 → 重启, 检查磁盘空间
- Milvus 挂 → 检查 etcd + minio 是否健康
- 文档未发布 → 走审批流到 PUBLISHED

### C.2.3 RAG NameError

**症状**: 嵌入失败时, RAG 整个崩溃, 5% 概率生产事故.

**原因**: 旧版 `for t in (bm25_task, vector_task)` 引用 vector_task 时未赋值.

**修复**: 当前版本已修, 不会发生. 若遇到, 升级到最新代码.

## C.3 中间件故障

### C.3.1 Redis 不可达

**症状**: Bot 502 + Assist 502, 所有 Stream/Pub-Sub 失败.

**原因**:
- Redis 服务挂了
- 网络问题

**排查**:
```bash
redis-cli -h localhost -p 6379 ping
```

**修复**: `systemctl restart redis` 或检查 docker compose 中 redis 容器.

### C.3.2 PostgreSQL 迁移失败

**症状**: 启动期 `alembic upgrade head` 失败, 服务无法启动.

**原因**:
- 迁移文件冲突
- 数据库 schema 不一致

**排查**:
```bash
cd agent && poetry run alembic current
poetry run alembic history
```

**修复**:
- 单步回滚: `poetry run alembic downgrade -1`
- 强制到 head: `poetry run alembic upgrade head --sql > migrate.sql` 检查 SQL

### C.3.3 Kafka 不可达

**症状**: Kafka 相关功能异常 (当前项目仅 verify 阶段使用, 业务无影响).

**修复**: 临时方案, 业务路径不依赖 Kafka, 可忽略. 长期方案见 Kafka 启用 sprint.

### C.3.4 MinIO 不可达

**症状**: 文件上传 502, 但检索仍可用.

**原因**:
- MinIO 服务挂了
- bucket `lumio-docs` 不存在

**修复**:
- 重启 MinIO
- 重建 bucket: `cd agent && poetry run python scripts/init_minio.py`

### C.3.5 ES / Milvus 启动顺序错乱

**症状**: ES/Milvus 启动后, 业务服务启动失败.

**原因**: Milvus 严格依赖 etcd + minio 健康后才拉起 (depends_on service_healthy), 若 etcd 慢, Milvus 启动失败.

**修复**:
```bash
docker compose -f deploy/docker-compose.yml ps
# 等所有 healthy 后再启动业务
docker compose -f deploy/docker-compose.yml logs etcd
```

## C.4 部署与配置

### C.4.1 端口冲突

**症状**: `Bind: address already in use`.

**排查**:
```bash
lsof -i :8000  # Bot
lsof -i :8001  # Assist
lsof -i :5432  # PG
```

**修复**:
- Bot/Assist 冲突 → 改 `LUMIO_BOT__PORT` / `LUMIO_ASSIST__PORT` env (虽然当前没暴露)
- PG/Redis 冲突 → 改 `POSTGRES_PORT` / `REDIS_PORT`

### C.4.2 启动顺序错乱

**症状**: 业务服务启动期 `init_db` 失败, 报 `connection refused`.

**原因**: 中间件未就绪.

**修复**:
```bash
# 1. 等中间件 healthy
make verify
# 2. 再启动业务
make dev
```

### C.4.3 demo 启动失败

**症状**: `make demo` 拉起后, 业务 API 502.

**排查**:
```bash
docker compose -f deploy/docker-compose.demo.yml logs -f lumio-bot
```

**修复**: 看具体错误, 通常是中间件未就绪, 等 30s 重试.

## C.5 安全合规

### C.5.1 JWT 解码失败 (`1001 AuthenticationError`)

**症状**: API 返 401, 客户端提示"未登录".

**原因**:
- token 过期 (默认 24h)
- token 签名错误 (secret 不一致)
- dev bypass 没开

**排查**:
```bash
# 解码 token 看 payload
poetry run python -c "import jwt; print(jwt.decode('eyJ...', 'lumio-dev-secret-change-in-production', algorithms=['HS256']))"
```

**修复**:
- 过期 → 重新登录
- secret 不一致 → 确认 `LUMIO_JWT_SECRET` 与签发端一致
- dev bypass → `LUMIO_DEV_AUTH_BYPASS=true` + bind localhost

### C.5.2 RBAC 拒绝 (`1003 AuthorizationError`)

**症状**: API 返 403, 客户端提示"权限不足".

**原因**: 角色不匹配, 例如 `customer` 角色访问 admin 端点.

**修复**:
- 用对角色登录
- 业务层判断角色与端点匹配

### C.5.3 限流触发

**症状**: API 返 429, 客户端频繁提示"操作频繁".

**原因**:
- 全局 60/min
- 聊天 30/min

**排查**: `LUMIO_RATE_LIMIT_CHAT=30/minute` 是默认值.

**修复**:
- 提升限流 (生产环境, 改 60/min)
- 客户端实现重试 + 指数退避

### C.5.4 PII 脱敏规则违反

**症状**: 日志中不应出现手机号, 但出现了.

**原因**:
- 业务代码直接拼接用户输入到日志, 没走 PIIMaskFilter
- 日志输出到第三方 (Loki/Sentry) 时, 第三方没做脱敏

**修复**:
- 用 `logger.info("user input", extra={"text": mask_pii(text)})`
- Loki 侧用 pipeline 做二次脱敏

### C.5.5 健康检查暴露敏感信息

**症状**: `/api/health/ready` 返 `{"redis_unreachable": "ConnectionRefusedError: 192.168.1.10:6379"}`.

**原因**: 旧版 `str(e)[:100]` 直接吐客户端.

**修复**: 当前版本只返 7 个分类码 (`redis_unreachable` / `postgres_unreachable` / ...). 若遇到, 升级到最新代码.

## C.6 可观测性

### C.6.1 跨服务 trace 断裂

**症状**: Jaeger 里 Python → Java 的 trace 不连.

**原因**:
- `HTTPXClientInstrumentor` 没装
- 跨服务没传 `traceparent` 头

**排查**:
```bash
# 1. 检查 Python 探针
poetry run python -c "from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor; HTTPXClientInstrumentor().instrument()"
# 2. 检查 Java 端
curl http://localhost:8090/actuator/metrics | grep tracing
```

**修复**:
- Python: 确认 `instrument_app(app, "lumio-bot")` 调用
- Java: 确认 `MANAGEMENT_TRACING_SAMPLING_PROBABILITY=0.1`

### C.6.2 指标缺失

**症状**: Grafana dashboard 某 panel 显示 "No data".

**原因**:
- Prometheus 抓取失败
- 指标命名不一致
- 业务代码没打点

**排查**:
```bash
# 1. 查指标是否被采集
curl http://localhost:9090/api/v1/query?query=up
# 2. 业务端
curl http://localhost:8000/metrics | grep lumio_
```

**修复**:
- Prometheus 配置: 检查 `config/prometheus.yml` scrape job
- 业务端: 补打点 + 重启

### C.6.3 Grafana dashboard 报错

**症状**: dashboard 加载失败, "Template variable not found".

**原因**: dashboard JSON 引用了不存在的 metric.

**修复**: 跑 `make verify-observability` 静态校验, 确认 dashboard 引用的指标都已在代码中定义.

## C.7 会话状态机

### C.7.1 会话状态非法转换 (`3005 InvalidTransitionError`)

**症状**: Bot 转换 phase 失败, 客户端返 422.

**原因**: 不在 `VALID_TRANSITIONS` 白名单.

**排查**:
```python
# models.py:VALID_TRANSITIONS
print(VALID_TRANSITIONS)
```

**修复**: 业务代码确保转换合法, 否则抛 4xx 让客户端处理.

### C.7.2 CAS Lua version 冲突

**症状**: 多个实例同时改同一会话, 偶发 StateConflictError.

**原因**: CAS 冲突, 正常现象.

**修复**: 框架自动重试 1 次, 通常透明. 频繁出现说明业务热点.

### C.7.3 ZSET 超时未触发

**症状**: 客户 30 分钟无消息, 仍未自动结束.

**原因**: 5s 轮询进程崩了.

**排查**:
```bash
ps aux | grep session_timeout
```

**修复**: 重启服务, 检查 ZSET 是否有数据: `redis-cli ZRANGE lumio:session:timeouts 0 -1 WITHSCORES`.

## C.8 MCP 工具

### C.8.1 MCP 连接失败

**症状**: Bot `_handle_tool` 走 RAG 兜底, 工具不响应.

**原因**:
- `MCP_ENABLED=false` (默认)
- Higress 网关不可达
- Java MCP Server 启动失败

**排查**:
```bash
# 1. 检查 MCP 开关
echo $MCP_ENABLED
# 2. 直连 Java MCP
curl http://localhost:8090/sse
# 3. 看 mcp_connection_state 指标
curl http://localhost:8000/metrics | grep mcp_connection_state
```

**修复**:
- 改 `MCP_ENABLED=true`
- 重启 Higress + MCP Server

### C.8.2 工具调用超时

**症状**: 调 `report_card_lost` 5s 超时, 客户没收到结果.

**原因**:
- Java 端业务逻辑慢
- 网关超时设置过短

**修复**:
- Java 端优化 mock 数据查询
- 网关超时改 10s (默认 10s, 调到 30s)

### C.8.3 工具护栏拒绝 (`tool_guard_denials_total` 飙升)

**症状**: 客户想调额, 提示"金额超限".

**原因**: 金额 > 角色限额 (例如 customer 调临时额度限 5 万).

**修复**:
- 业务侧: 调小金额
- 护栏侧: 调大限额 (需合规审批)

## C.9 测试与 CI

### C.9.1 35 errors 持续

**症状**: CI 跑 pytest 一直 35 failed.

**原因**:
- RecursionError (test_retrieval 部分用例)
- 中间件未起, e2e skip
- 启动超时

**修复**:
- 短期: 接受现实, 55% 覆盖率门槛
- 长期: 修掉 RecursionError 后, 提到 60% 门槛

### C.9.2 mypy 错误累积

**症状**: 171 → 200 个 mypy 错误, 还在涨.

**原因**: mypy advisory 模式不阻塞 CI, 错误自然累积.

**修复**: 按模块拆分, 指定 owner 季度集中收敛.

### C.9.3 e2e 失败

**症状**: main 分支 e2e job 失败, 提示 `/api/chat/send` 返 5xx.

**原因**:
- Demo 启动慢, 业务 API 没就绪
- 中间件 (Redis/PG) 容器没起

**修复**: 检查 `docker compose -f deploy/docker-compose.demo.yml ps`, 等全部 healthy.

## C.10 性能调优

### C.10.1 Bot P99 延迟高

**排查**:
- Grafana `lumio_bot_response_duration_seconds` p99
- 拆解: 分类延迟 / RAG 延迟 / LLM 延迟

**调优**:
- 分类慢 → 改用更小 LLM (qwen2.5:0.5b)
- RAG 慢 → 检查 ES 索引 / Milvus nprobe
- LLM 慢 → 启用 GPU

### C.10.2 Assist 引擎超时 (>5s)

**排查**:
- 看哪一阶段慢 (D1/D2/D3/E1/E2/E3)
- 多数情况是 E1 (RAG + LLM 三路并行)

**调优**:
- 调小 `ORCH_E1_SLA_SECONDS` (当前 3s)
- 减少 E1 chunks 数量 (top_k=5 → 3)

## C.11 应急联系

| 类别 | 联系人 | 渠道 |
|---|---|---|
| LLM/Ollama 故障 | LLM 平台组 | Slack #llm-platform |
| 银行核心系统 | 银行业务组 | Slack #bank-core |
| 数据库 | DBA 团队 | Slack #dba |
| 监控告警 | SRE 团队 | Slack #sre |
| 安全事件 | 安全组 | security@lumio.io |

---

## C.13 客服 Agent 场景 (第 15-17 章配套)

> 以下 8 个问题来自第 15-17 章的客服 Agent 能力层实战, 配套深挖文档 [第 15 章 上下文工程](../chapters/15-context-engineering.md) / [第 16 章 客户记忆与知识图谱](../chapters/16-customer-memory-and-kg.md) / [第 17 章 工具调用与确认状态机](../chapters/17-tool-calling-and-confirmation.md).

### C.13.1 客户说"Bot 不知道我是白金卡"

**症状**: VIP 客户来电, Bot 仍问"您是什么卡?", 体验差.

**根因排查**:

1. **检查 `dialogue_log` 表是否真有该 customer_id 的 90 天数据**:
   ```sql
   SELECT COUNT(*) FROM dialogue_log
   WHERE customer_id = 'C12345' AND speaker = 'customer'
   AND created_at >= NOW() - INTERVAL '90 days';
   ```
   - 返回 0: 客户是新客户或 dialogue_log 没写入 → 走 16.11 案例
   - 返回 N>0: 走下一步

2. **检查 `apply_learned_profile` 是否真应用**:
   ```bash
   grep "客户画像已应用" app.log | grep "C12345"
   ```
   - 无输出: 学习失败, 检查 PG 是否可达 + `string_agg` 是否报错
   - 有输出: 检查 `state.vip_level` 是否被显式设置覆盖

3. **检查显式覆盖**: 客户在当前会话说过"我是新卡" → `state.vip_level != "普通"` → 学习结果不覆盖. 这是 by-design 行为, 需客户语境化

**修复**: 见 [第 16.10 节 已知问题与改进项](../chapters/16-customer-memory-and-kg.md#1610-已知问题与改进项) — 缺 `ix_dialogue_log_customer_time` PARTIAL INDEX.

### C.13.2 工具调用超过 5 轮 (RuntimeError)

**症状**: Bot 偶发 `RuntimeError: 工具调用超过最大轮数 5`, 用户看到 500.

**根因排查**:

1. **查看 LLM 是否死循环**: 拉对应 session 的 audit_log, 看连续 5 个 `tool.*` 调用的 tool_name 序列
   ```sql
   SELECT created_at, action, detail
   FROM audit_log WHERE session_id = '...' AND action LIKE 'tool.%' ORDER BY created_at DESC LIMIT 10;
   ```
2. **典型循环模式**:
   - LLM 反复调 `query_credit_limit` 查同一额度 (LLM 觉得 "再查一次可能更新了")
   - LLM 调 `query_installment_offer` 后再调 `query_installment_status` 再调 `query_installment_offer` (幻觉)
3. **是否触发了 RAG 降级链**: `_handle_tool` 失败时回落 `_handle_knowledge` 也会走 5 轮工具 (虽然本不会), 但 `bot_agent.py:375` 走 knowledge_agent → 重新拼 system_prompt + RAG, 不应该循环

**修复**:
- 短期: 业务方加 LLM prompt "调同一个工具不要超过 2 次"
- 中期: 把 `max_tool_iterations` 调到 3 + 加 LLM 提示
- 长期: 引入"工具调用计划" — LLM 一次性规划所有工具调用, 不循环

### C.13.3 5 态确认状态机卡在 unclear

**症状**: 客户回复"嗯" / "哦" / "好" → Bot 一直追问"请明确回复确认或取消", 体验差.

**根因排查**:

1. **关键词词典覆盖不全** (`tool_executor.py:46-72`):
   - `_CONFIRM_KEYWORDS`: 确认/确定/是的/好的/可以/继续/同意/办理/ok/yes
   - 客户说"嗯" / "哦" / "行" / "中" / "妥" → 都不命中
2. **是否含敏感关键词被误判**: 客户说"我**不**确认" → cancel 优先命中 (L83) → 正确
3. **大小写**: 已 `.strip().lower()` 处理, 无此问题

**修复**:
- 短期: 加关键词 "嗯" / "哦" / "行" / "中" / "妥" (确认类)
- 中期: 用 LLM 二次判定 (50ms 延迟可接受)
- 长期: 引入"模糊确认"识别 — 客户说"行吧"虽含"吧"但语义是 confirm

### C.13.4 知识图谱注入失效 (无 `## 知识图谱补充信息` 段)

**症状**: LLM 答客户问"信用卡额度"时没看到 KG 关系补充.

**根因排查**:

1. **是否在 knowledge_agent 分支**: `bot_agent.py:213-216` 仅 `_handle_knowledge` 调用 KG
   - 业务类 (`_handle_business`) 调 MCP 工具, **不调 KG**, 这是 by-design
2. **RAG 是否成功**: `bot_agent.py:213` `if context:` — RAG 失败时空 context 跳过 KG
   ```python
   context = await self._retrieve(user_input)
   if context:  # ← 空 context 跳过
       context = enrich_retrieval_context(user_input, [context])
   ```
3. **实体名是否匹配**: `knowledge_graph.py:90` `if entity_name in query_text` — 5 实体是高频词
   - 客户说"额度" → "信用卡" 不在 query_text → 只命中"额度"实体
   - 客户说"信用卡" → 命中"信用卡"实体
   - 客户说"提额" → 5 实体都不在 query_text (因"额度" 不在"提额"中) → **0 命中**

**修复**: 见 16.7.2 潜在改进 — `if entity_name in query_text or query_text in entity_name` 捕获"提额"→"额度"近义.

### C.13.5 对话摘要没生成 (LLM 不可用)

**症状**: `_load_history` 触发 `_ensure_summary` 但摘要始终为空, `_build_session_memory` 没 `[对话摘要]` 段.

**根因排查**:

1. **LLM 不可用**: `bot_agent.py:684-685` `if llm_client is None: return` → 摘要静默跳过
2. **LLM 调用 3s 超时**: `bot_agent.py:693` `timeout=3.0` → `except Exception: return` (L695-697)
3. **`last_summarized_turn_id` 错位**: LTRIM 删了已摘要 turn, split_idx 始终 0 → 整段 trimmed 都摘要 → 慢
4. **CAS patch 失败**: `bot_agent.py:721` `logger.warning("对话摘要 CAS 写入失败")`

**修复**:
- 检查 LLM 服务 (`make verify`)
- 检查 session_meta 写权限 (Redis ping)
- 长期: 摘要失败时记录到 `lumio:summary_failed:{session_id}` 计数, 累积 3 次触发告警

### C.13.6 槽位追踪器意图切换时清空 (高优先级体验问题)

**症状**: 客户从"问账单"切换到"问积分", Bot 重新问"卡号后四位"等已收集过的信息.

**根因排查** (`bot_agent.py:799-802`):

```python
if raw:
    data = json.loads(raw)
    if data.get("intent") == intent.value:  # ← 仅当意图不变才复用
        tracker = SlotTracker.from_dict(data)
    # 意图不同 → tracker 保持 None → 创建新的 (L807)
```

**by-design 行为**: 槽位是意图绑定的, 切换意图就重新追踪. 但客户期望"已收集的卡号不应该重复问".

**修复方案**:
- 短期: entity_pool 跨意图保留, Bot 通过 entity_pool 自动填充
- 中期: 槽位追踪器改为"全局槽位"模式 (L65 `_ENTITY_TO_SLOT` 已有基础)
- 长期: 槽位 + entity_pool 合并, 消除双层数据

### C.13.7 渐进式暴露不生效 (开关/阈值/映射 三重检查)

**症状**: 配置了 `intent_tool_map` 也开了 `progressive_disclosure_enabled=True`, 但 LLM 仍收到 22 个工具.

**根因排查** (按顺序):

1. **`progressive_disclosure_enabled` 是否真为 True**: 检查 `MCPSettings.__dict__` 或 Nacos 配置
2. **`pd_confidence_threshold` 是否过高**: 实际意图置信度 < 阈值 → 返回 None → 暴露全量
   - 调小阈值 (0.5) 测试
3. **`intent_tool_map` 是否覆盖了实际意图**: `bill_query` 没配置 → 返回 None → 暴露全量
   - 检查 `config.py:417-435` 配置
4. **意图分类是否正确**: 实际分类是 `faq` → 不在 `TOOL_INTENTS` → 走 knowledge_agent → 不调工具
5. **是否有 bot_agent 路径问题**: 业务类 (`_handle_business`) 直接调 MCP, **不经过 `select_tools_for_intent`**, 所以全量工具仍可用

**修复**: 上述任一命中即按需调整.

### C.13.8 护栏拒绝但用户无感知 (拒绝话术 + 审计)

**症状**: `TOOL_GUARD_DENIALS` 指标持续增长, 但用户报"Bot 不办事", 不知道发生了什么.

**根因排查**:

1. **拒绝话术统一**: `tool_executor.py:41` `_GUARD_REFUSAL = "很抱歉, 该操作目前无法为您办理. 如需帮助, 我可以为您转接人工客服."` — 不告诉用户**为什么**拒绝
2. **审计补全**: `tool_executor.py:378-388` 写 `status_code=403` 审计, 含 `decision.reason` (内部)
3. **用户视角无感**: 设计上"安全优先, 不暴露内部信息" — 但客户以为"Bot 不行"

**修复**:
- 短期: 给运营提供 `_GUARD_REFUSAL` 替代话术 (如"金额超过 XX 元上限, 建议前往柜台办理")
- 中期: 区分 `role_denied` vs `amount_exceeded` 给不同话术 (role 类继续统一话术, amount 类可给阈值提示)
- 长期: 客户"为什么"点击 → 弹窗显示具体原因 (但需鉴权, 不暴露给攻击者)

---

> **下一步**: 回到 [README.md](../README.md) 看其他章节.
