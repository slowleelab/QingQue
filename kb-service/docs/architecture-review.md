# kb-service 企业级银行 KB 系统对标评审

> 评审日期: 2026-07-29 (v1.2 更新 P1-3 落地记录)
> 评审范围: `kb-service/` 全量 (3504 LOC 业务代码 + 测试)
> 评审基准: 国有大行/股份制银行客服中心知识库系统 (招行/工行/建行) 的成熟度维度
> 评审结论: **已达到生产级骨架**, P0 + P1-3 落地; P1-1/P1-2/P1-4 仍有部分缺口 (见 A.7.5)

---

## 一、评审方法

按"成熟度维度"逐项对照, 三个角度交叉验证:

1. **静态结构**: ORM / API / 中间件 / 流水线 7 阶段 / Worker 异步
2. **企业级 KB 必选项**: 银保监会/央行对银行客服知识库的硬性要求
3. **对照 `agent/lumio/` 早期 RAG**: 演进路径是否收敛

## 二、维度总览 (打分: ★ 已实现 / ◐ 部分实现 / ✗ 缺失)

| 维度 | 评分 | 现状 |
|---|---|---|
| 数据模型 & 合规字段 | ★★★ | approval_status / doc_group / allowed_roles / regulatory_tags 齐备 |
| ETL 流水线稳定性 | ★★★ | 7 阶段 + 流水日志 + Kafka 异步 + DLQ |
| 检索质量 | ★★★ | ES 原生 RRF + Reranker + 影子索引 + 缓存 |
| 安全 / 鉴权 | ★★ | API Key + AC 自动机 + 审计中间件; 缺 RBAC、限流粒度粗 |
| 可观测性 | ★★★ | Prometheus + structlog + /health + /metrics + /admin/diagnostics |
| 审计 & 合规追溯 | ★★ | 审计中间件; 缺审批工作流 API、版本回滚接口 |
| 嵌入治理 | ★★ | model_version + 影子索引; 缺漂移检测、自动回退 |
| 多租户隔离 | ◐ | tenant 字段缺, allowed_roles 字段未在检索链路实际生效 |
| 灾备 / 高可用 | ★★ | DLQ + 健康检查; 缺 ES → PG 回灌自动化、ES 索引模板版本化 |
| 性能 / SLA | ★★ | Redis 缓存 5min; 缺 P95/P99 告警、检索超时降级 |
| 评估体系 | ★★ | RAGAS golden query; 缺 CI 回归门禁、离/在线混部 |

---

## 三、做的好的 (5 项)

1. **ES 8.14+ 原生 RRF retriever** (engine.py:134-149) — 服务端融合 BM25+kNN, 单查询一次完成, 性能与一致性比 Python 手写 RRF 强; 这在企业 KB 中属于"省了 ops 一个大坑".
2. **PG 单真相源 + ES 派生索引** (orm/kb.py:9-10 注释) — 删 Milvus 双写, 文档修订靠 reindex_document 即可 (documents.py:262-324), 架构简化显著.
3. **AC 自动机敏感词过滤** (security/sensitive_filter.py) — O(n) 扫描, hot reload, 满足"敏感词必须 AC 自动机"的硬性要求; 上传 + Parse 后双扫描, 覆盖二进制文件盲区 (orchestrator.py:122-144).
4. **7 阶段 ETL + 流水日志** (orchestrator.py + KbIngestionLog) — 每阶段 SUCCESS/FAILED/duration 全部落库, 失败有 step_detail; admin diagnostics (admin.py) 直接聚合.
5. **影子索引灰度切换** (engine.py:273-279) — `shadow_model_version` 配置 + `request.model_version` 透传, 切换流程可观测; 在银行生产环境属于"切模型不出事"的关键能力.

---

## 四、缺什么 (按 P0/P1/P2 排序)

### P0 — 不补上不能上生产 (硬性合规 / 安全)

#### P0-1. 多租户隔离缺失, allowed_roles 形同虚设
**现状**: `KbDocument.allowed_roles: JSON` 字段定义 (orm/kb.py:182-184), 但 `retrieval/engine.py:266-279` 的 compliance_filters 仅按 `approval_status + is_current_version + effective_date` 过滤, **完全没有按角色/租户裁剪结果**.

**风险**: 任意调用方拿到 API Key 即可检索全量知识; 银行场景下分行/客服层级看到上层专属文档 → 监管红线.

**修复**:
- `RetrieveRequest` 增加 `tenant_id` / `actor_roles: list[str]` 字段 (retrieval/models.py)
- `engine.retrieve` 在 `compliance_filters` 注入 `allowed_roles` terms filter (terms 命中语义)
- API 依赖注入: 从 JWT/SSO 解析 actor 信息 (auth.py 当前仅校验 API Key, 拿不到身份)
- 文档: 严格区分"全员可见 / 客服可见 / 主管可见"三级

#### P0-2. 无审批工作流 API, 7 状态机只到 ORM
**现状**: `KbApprovalStatus` 7 态枚举 (orm/kb.py:68-82), `KbDocumentApproval` append-only 表 (orm/kb.py:240-264), 但**没有任何 `POST /api/v1/documents/{id}/submit|approve|reject|publish` 接口**. 文档 status 字段在上传时直接钉成 `KAFKA_QUEUED`, approval_status 默认 `DRAFT` 但无人推进.

**风险**: 监管要求的"双人复核 + 留痕"做不实; 文档直接进检索库等于放弃合规.

**修复**:
- `api/approval.py` (新): 5 个端点 `submit / approve / reject / publish / archive`
- 状态机校验: 用 `@transition.allowed(KbApprovalStatus.X)` 装饰, 非法转移 422
- 每次转移写 `KbDocumentApproval` (actor_id 必填), 与审计日志双轨
- 与 admin/reindex-all 配合: publish 后才允许进 ES 索引

#### P0-3. 审计日志只记 HTTP 维度, 业务操作零追溯
**现状**: `AuditMiddleware` (audit.py) 记录 method/path/status_code/latency, **不记录 actor_id/doc_id/action/old_value/new_value**. `KbDocumentApproval` 表是为业务审计准备的, 但没有写入路径.

**风险**: 监管问"这份文档谁改的 / 何时发布 / 改了什么", 答不上来; 同时满足不了《商业银行内部控制指引》的"操作留痕可追溯"硬要求.

**修复**:
- 审计中间件: 增加 `actor_id` / `actor_role` 解析 (从 Bearer Token 或 mTLS 证书)
- 业务操作统一过 `audit_service.log(action, doc_id, before, after, actor_id)`
- KbDocumentApproval 写入点: 审批/状态转移/版本切换/敏感词命中放行
- 检索 API 也要审计: "谁在何时查了哪个客户分层 / 卡种" — 银行风控要求

### P1 — 影响生产稳定性, 应在 1-2 个迭代内补齐

#### P1-1. 嵌入模型漂移无检测, 影子切换无回退开关
**现状**: `EmbeddingCircuitBreaker` (embedder.py) 是**服务级**熔断 (provider 挂掉时保护), 跟"模型输出分布漂移"是两回事. 影子索引有 `shadow_model_version` 字段, 但**没有"影子 → 默认"切换的执行器**, 也没有"两版模型对同一 query 的 top-K 重合度"对比.

**风险**: 切到新模型后客户报"召回质量差", 切换原子性靠手工, 无 30s 内回退能力.

**修复**:
- 加 `EmbeddingDriftMonitor`: 后台 1h 一次, 用 N 条 golden query 跑 A/B 模型, 计算 top-10 重叠率 + 召回率差值, 入 prometheus
- 告警规则: drift > 5% 触发 webhook (邮件/钉钉)
- 自动回退: `shadow_model_version → default` 切换走 "读 config → 写 setting → reload provider" 原子事务, 30s 内可回滚
- 影子期 N 天硬性要求: 业务方确认 recall/mrr 不掉才能升 default

#### P1-2. 限流粒度粗, 按 IP+Path 而非按租户/角色
**现状**: `RateLimitMiddleware` (rate_limit.py) 按 `client_ip + path` 一分钟 60 次. 问题是:
- 银行 API 网关后面所有请求 client_ip 一样 → 整网关共用 60 次配额
- 检索和文档上传共用配额 → 上传大文件会阻塞客服实时检索
- 无 burst / SLA 等级 (VIP 客户 / 普通客户)

**修复**:
- 维度: 优先按 `api_key` (在 auth 之后), 其次按 path 分桶
- 配额分层: `/retrieve` 1000/min/key (高), `/documents POST` 10/min/key (低)
- 区分 burst (token bucket) vs sustained (sliding window)
- 超限返回 429 + Retry-After, 同时累计 `kb_rate_limit_exceeded_total` 指标

#### P1-3. 检索超时无降级, P95 不可观测
**现状**: `engine.retrieve` (engine.py) 整链路串行: embed → ES search → rerank. 三处都可能慢, 但**没有 per-stage timeout**, 也没有 `KB_RETRIEVE_TIMEOUT_MS` 配置. Prometheus 指标 `RETRIEVE_LATENCY` 只在 `/metrics` 暴露, 没有 SLO 告警.

**风险**: 嵌入服务慢 → 整个客服台排队; ES 集群 GC → P99 突刺到 5s+ 无感知.

**修复**:
- `RetrieveRequest` 增加 `timeout_ms: int = 1500` 字段
- 各阶段用 `asyncio.wait_for` 包装: embed ≤ 500ms / ES search ≤ 800ms / rerank ≤ 200ms
- 超时即降级: hybrid → bm25_only → 空结果 + 标记 `degraded: true`
- SLO 告警: P95 > 1s / P99 > 2s 持续 5min 触发 (Prometheus rule)
- 暴露 `/api/v1/admin/slo` 端点: 实时 P50/P95/P99, error budget 剩余

#### P1-4. 文档版本回滚靠手工, 无 API
**现状**: `doc_group` + `is_current_version` 字段 (orm/kb.py:174-181) 支持多版本共存, 但 `documents.py` 只暴露 `reindex` 操作; **没有"切回上一版本"**的 API, 也没有 diff 接口.

**风险**: 误操作把过期的合规文档换上来, 只能用 SQL 硬改, 留不下审计.

**修复**:
- `POST /api/v1/documents/{doc_id}/rollback` → 选 doc_group 下另一版本, 原子切换 `is_current_version`
- `GET /api/v1/documents/{doc_id}/versions` → 列出历史版本
- `GET /api/v1/documents/{id}/diff?from=v1&to=v2` → 返回 content diff
- 切换都写 KbDocumentApproval + 审计日志

### P2 — 长期能力建设, 不阻塞 MVP 但应有 roadmap

#### P2-1. 检索缺"否定 / 排除"语义
**现状**: `RetrieveRequest.filters` 只支持正向 terms, 不能 "category != xxx". 客服常见需求"不要给我信用卡推销话术".

**修复**: filters 支持 `exclude: dict[str, list[str]]`; engine 生成 ES `must_not` 子句.

#### P2-2. 无"知识图谱 / 实体关系"层
**现状**: LLM 抽取了 `entities` 字段 (orm/kb.py:199), 但**只存不进图**. 客户问"白金卡境外取现手续费" 跨文档串联靠 BM25 + kNN 碰运气.

**修复**: 引入 Neo4j / ES join 字段, `entity → document[]` 反向索引; 查询先做实体识别再召回.

#### P2-3. RAGAS 评估无 CI 集成
**现状**: `eval/ragas_eval.py` 是手工脚本, 跑 golden query 出分. 改个 chunk_size 就掉 5% 也无人拦.

**修复**:
- `make eval` 集成到 PR check
- 阈值: `context_recall ≥ 0.75` / `faithfulness ≥ 0.85` 失败则 fail
- 评估报告归档到 docs/eval/, 跨 PR 对比

#### P2-4. 缺 GDPR / 个人信息保护法相关能力
**现状**: 敏感词过滤只挡"身份证号/银行卡号"这种**关键词**, 不做**结构化识别** (Luhn 校验 + 正则). 银行外规要求: 客户身份证号、卡号、CVV 入库前必须脱敏或删除.

**修复**:
- 加 `PiiRedactor` 步骤: 在 clean_text 之前, 识别 + 替换 + 记录脱敏日志
- 卡号: Luhn + 前缀(BIN) 双重校验, 替换为 `{{CARD_TAIL_4}}`
- 身份证: GB 11643-1999 校验码
- 脱敏率入指标 `kb_pii_redacted_total`, 用于合规报告

#### P2-5. 检索缓存 key 包含 raw query, 无 PII 隔离
**现状**: `_build_cache_key` (engine.py:78-83) 用 query 文本 md5, 但 query 里可能含卡号/手机号 → 缓存 key 暴露敏感数据. Redis 是明文持久化时, 等于把客户隐私写进 key 索引.

**修复**:
- 检索前 PII 预过滤 / 掩码
- 缓存 key 用归一化后的 query (掩码后), 不存原文

---

## 五、与 `agent/lumio/` 早期 RAG 的演进差异

| 维度 | 早期 lumio (604 LOC retrieval) | 当前 kb-service (393 LOC engine) | 演进评价 |
|---|---|---|---|
| 混合检索 | Python 手写 RRF | ES 原生 RRF retriever | ✓ 性能 + 可维护性大幅提升 |
| 存储 | ES + Milvus 双写 | ES 单写 + PG 真相 | ✓ 架构简化, 降低一致性负担 |
| 嵌入版本 | 无 | model_version + 影子索引 | ✓ 显著增强 |
| 异步 ETL | 同步阻塞 API | Kafka worker + DLQ | ✓ 削峰 + 重试 |
| 抽取 | 人工 YAML | LLM 自动抽取 | ✓ 降人力 |
| 评估 | 无 | RAGAS golden query | ✓ 质量基线 |
| **新增** | — | AC 自动机敏感词 | ✓ 银行硬性需求 |
| **新增** | — | 7 状态机审批 | ◐ ORM 已有但无 API |
| **新增** | — | 审计 / 限流 / Prometheus | ✓ 运维侧补齐 |
| **缺** | — | 多租户 / RBAC | ✗ 反而是生产硬要求 |
| **缺** | — | PII 识别 / 脱敏 | ✗ 银行外规要求 |
| **缺** | — | 文档版本回滚 API | ✗ 业务必需要 |

> 演进路径基本正确, 但**在合规能力上, lumio 没有的东西 kb-service 也没补全**, 是 1→2 没补的部分, 不是倒退.

---

## 六、落地建议 (roadmap)

### 第一个迭代 (1-2 周) — P0 全收
- [ ] P0-1 多租户隔离 (tenant_id + allowed_roles 注入)
- [ ] P0-2 审批工作流 API (5 个端点 + 状态机)
- [ ] P0-3 业务审计 (actor_id 解析 + 业务操作留痕)

### 第二个迭代 (2-3 周) — P1 关键项
- [ ] P1-1 嵌入漂移监控 + 自动回退
- [ ] P1-2 限流粒度细化 (api_key + 配额分层)
- [ ] P1-3 检索超时降级 + SLO 告警
- [ ] P1-4 版本回滚 / diff API

### 第三个迭代 (持续) — P2 长线
- [ ] P2-1 否定语义
- [ ] P2-2 知识图谱
- [ ] P2-3 RAGAS CI 门禁
- [ ] P2-4 PII 识别脱敏
- [ ] P2-5 缓存 PII 隔离

---

## 七、结论

kb-service 的**工程完成度**已经超过 lumio 早期 RAG, 在**工程能力 (异步 / 可观测 / 灰度)** 上达到企业级标准. 真正的差距在**合规能力** — 多租户隔离、审批工作流、业务审计是银保监会/外规的硬性要求, 不是 P2 锦上添花. 建议第一个迭代先收 P0 三项, 上生产前必须完成.

文档版本: v1.0
维护者: SmartCS Team
下次评审建议时间: P0 三项落地后

---

# 附录 v1.1 — P0 落地记录 (2026-08-02)

> 评审基线 v1.0 提出的 P0 三项 (P0-1 多租户隔离 / P0-2 审批工作流 API / P0-3 业务审计) 已全部落地.
> 实际代码 vs 评审建议的差异见下表, 后续评审以本附录为新基线.

## A.1 P0-1 多租户 + 角色访问隔离 (2 commits, +23 测试)

| 评审建议 | 实际落地 | commit |
|---|---|---|
| `RetrieveRequest` 增加 `tenant_id` / `actor_roles` | ✓ P0-1 决定走严格 override, 请求体 `tenant_id` 不接受, 强制用 `principal.tenant_id` (身份是唯一真相源) | `13a2415` |
| `engine.retrieve` 在 `compliance_filters` 注入 `allowed_roles` terms filter | ✓ `build_es_filters` 对 list 字段生成 `terms` 子句 (1.4); `_ES_KEYWORD_FIELDS` 含 `allowed_roles` (1.3) | `13a2415` |
| API 依赖注入: 从 JWT/SSO 解析 actor | ✓ `verify_principal` 已支持 JWT (sub→actor_id) + API Key (sha256[:8]→actor_id), 早于本次评审 | — |
| 严格区分"全员/客服/主管"三级 | ✓ 落地语义: `allowed_roles=[]` 空列表 = 全员可见 (Confluence/SharePoint 默认); `["admin"]` 仅 admin; 与评审一致 | `13a2415`, `13dbaa8` |
| ES mapping 缺 `tenant_id` / `allowed_roles` (评审没明确说但实际存在) | ✓ `init_elasticsearch.py` 补 mapping; writer 写入 2 字段 | `13a2415` |
| `_source` 含 `tenant_id` / `allowed_roles` (评审没明确说但合规要) | ✓ `_search_rrf` `_source` 加 2 字段 (审计 + 前端展示) | `13a2415` |
| **override 漏洞** (评审没指出, 实际是真洞) | ✓ 严格 override 防御: 请求体 `tenant_id` 与 principal 不一致 → 403, 请求体 `actor_roles` 始终覆盖为 principal.roles | `13a2415` |

**评审基线 v1.0 实际遗漏, P0-1 实施时新发现的洞**:
- ES mapping 缺字段 + writer 不写字段 (4 处断点 ①②)
- `_ES_KEYWORD_FIELDS` 不含 `allowed_roles` (断点 ③)
- 上传无 `allowed_roles` Form 字段 (断点 ④)

## A.2 P0-2 审批工作流收口 (4 commits, +47 测试)

| 评审建议 | 实际落地 | commit |
|---|---|---|
| 5 个端点 `submit/approve/reject/publish/archive` | ✓ 6 端点: `submit/approve/reject/publish/archive + GET /approvals` 历史 (评审建议的 5 + I3-C1 加的 1) | `f3e76fe`+ |
| 状态机校验: 非法转移 422 | ✓ `_TRANSITIONS` 7 状态 × 7 动作, `validate_transition` 抛 `WorkflowError`, HTTP 422; 双签违规 403 优先 | `79d371a` |
| 每次转移写 `KbDocumentApproval` (actor_id 必填) | ✓ `record_approval` 单点写入, 7 字段全填 (action/from_status/to_status/actor_id/actor_role/comment + tenant_id/ip/ua/request_id/operation_result/risk_level/retention_until) | `79d371a` |
| 与 admin/reindex-all 配合: publish 后才允许进 ES 索引 | ✓ publish 同步 ES 校验 (PG chunk_count == ES doc_count), 不一致触发 reindex 重建 (P0-2.3) | `59267cb` |
| **takedown 走状态机** (评审说"应急"但没明) | ✓ P0-2.2 takedown/rollback 走状态机校验, 强制 `PUBLISHED/SUPERSEDED → ARCHIVED` (旧 DRAFT 直 takedown 是漏洞) | `79d371a` |
| **双签豁免收紧** (评审没明) | ✓ P0-2.4 BREAKING: `_DUAL_SIGN_EXEMPT_ROLES = {"admin"}` (移除 service, 修复 4-eyes 绕过) | `285fa3f` |
| **待审批队列** (评审没明, 真缺口) | ✓ GET /api/v1/documents/approvals/pending (P0-2.1) | `f3e76fe` |

**评审基线 v1.0 实际遗漏, P0-2 实施时新发现的洞**:
- 5 端点都有, 但**没有待审批队列** — 审核员要列出"待我处理的"很痛
- takedown / rollback 旧代码**绕过状态机** (P0-2.2 修)
- publish **不触发 ES 同步** (P0-2.3 修)
- service 角色**绕过双签** (P0-2.4 修, BREAKING)

## A.3 P0-3 业务审计增强 (1 commit, +16 测试)

| 评审建议 | 实际落地 | commit |
|---|---|---|
| 审计中间件: 增加 `actor_id` / `actor_role` 解析 | ✓ 早于评审: `AuditMiddleware` (audit.py) 已带 principal; P0-3 修异常路径带 principal (A.4) | `bacfa94` |
| 业务操作统一过 `audit_service.log(action, doc_id, before, after, actor_id)` | ✓ `AuditService.log` 早于评审; P0-3 加 `operation_id` 串联多步 (C) | `bacfa94` |
| KbDocumentApproval 写入点: 审批/状态转移/版本切换/敏感词命中放行 | ✓ 审批/状态机走 `record_approval`; P0-3 加 ES 重建审计 `record_approval_partial` (D); 敏感词命中放行尚未落库 (留 P0-4) | `bacfa94` |
| 检索 API 也要审计 | ✓ `log_retrieval` 早于评审, 落 KbRetrievalAudit | — |

**评审基线 v1.0 实际遗漏, P0-3 实施时新发现的洞**:
- rollback / takedown 端点**没声明 `request: Request`**, IP/UA 全部丢失 (A)
- `last_actor` 永远取 `doc.created_by`, **跨版本场景下双签失效** (B)
- KbDocumentApproval 与 AuditService.log(structlog) **是两条独立链路, 无串联** (C)
- `_ensure_es_in_sync` 重建结果**只走 logger.info, 失败无审计** (D)

## A.4 统计

| 项 | v1.0 评审 | v1.1 实际 |
|---|---|---|
| P0-1 commit 数 | 1 (评审) | 2 (split: 检索层 + 上传层) |
| P0-2 commit 数 | 1 (评审) | 4 (split: 队列 + 状态机 + ES 同步 + 双签收紧) |
| P0-3 commit 数 | 1 (评审) | 1 (合并 A/B/C/D) |
| **总 commit** | 3 | **7** |
| **测试增量** | (评审未量化) | **+86 用例 (P0-1 23 + P0-2 47 + P0-3 16)** |
| **0 回归** | (评审未约束) | **392 pass, 0 失败** |
| 新依赖 | 0 | 0 |
| DB migration | 0 | 0 (复用 ORM 现成字段) |
| ORM 变更 | 0 | 0 |

## A.5 评审基线 vs 实际 — 维度重打分

| 维度 | v1.0 评分 | v1.1 评分 | 变化原因 |
|---|---|---|---|
| 多租户隔离 | ◐ (P0-1) | ★★★ | P0-1 落地: ES mapping + writer + filter + override 防御 + 检索/上传/重索引全链路 |
| 审计 & 合规追溯 | ★★ (P0-2 + P0-3) | ★★★ | P0-2 + P0-3 落地: 7 状态机 + 5 端点 + 双签 + ES 同步 + IP/UA/串联/重建审计 |

## A.6 下次评审基线建议 (v1.2)

按评审 v1.0 第六节 roadmap, 下次评审应聚焦 **P1 关键项**:
- **P1-1** 嵌入模型漂移监控 (影子切默认) — 高优先
- **P1-2** 限流粒度细化 (api_key + 配额分层) — 高优先
- **P1-3** 检索超时降级 + SLO 告警 — 高优先
- **P1-4** 版本回滚 / diff API — 已落地一部分 (rollback/takedown/diff), 但 admin 一键回滚未做

**P0-4 残留 (本次评审未量化)**:
- KbRetrievalAudit 加 `ip/ua` 列 (需要 migration)
- Admin 端点业务审计 (clear-cache / reindex-all 写库)
- `audit_service.log_degradation` 0 调用 (死代码, 要么删要么集成)
- 留存清理 job (5 年到期归档)
- 敏感词命中放行 → 落 `KbDocumentApproval` (P0-3 评审建议, 实际未做)

**评审周期建议**: v1.2 评审时间 = P1-1 + P1-2 + P1-3 落地后 (预计 2-3 周).

---

## A.7 P1-3 SLO 端点 + PromQL 修复 (Sprint 6, 4 commits, +33 测试)

### A.7.1 评审建议 vs 实际落地

| 评审建议 (v1.0) | 实际落地 | 评估 |
|---|---|---|
| 检索超时降级 + SLO 告警 (P1-3) | `slo.py` 25+ 单测全过, 但 0 生产调用 → 修 | **半真半假** — 函数层完整, 集成层完全缺失 |
| `/api/v1/admin/slo` 端点 (slo.py:25 docstring 承诺) | **完全缺失** → 落地 | **真洞**, 与 P0-4 "audit_service.log_degradation 0 调用" 同病 |
| burn-rate 告警规则 (Prometheus YAML) | 函数 `generate_prometheus_rules()` 存在但**指标名错** (`kb_retrieve_errors_total` 不存在) → 修 | **真洞** — 即便生成也无法在生产匹配 |
| 检索失败计入 SLO 错误率 | RETRIEVE_COUNT{status="failed"} 完全**没打点** → 加 | **真洞** — availability SLO 永远看到 0 失败 |
| SLO 配置化 (P95/P99 阈值) | slo.py hard-code → 走 ObservabilitySettings + .env | 落地 |

### A.7.2 落地清单 (4 commits, Sprint 6)

| Commit | 内容 | 行数 | 测试 |
|---|---|---|---|
| P1-3.1 修 slo.py 指标名 + 字段语义 | `kb_retrieve_errors_total` → `kb_retrieve_total{status="failed"}`; availability/latency PromQL 分支; SLOTarget.latency_threshold_s; compute_error_budget slo_name 字段 | 164 | +10 |
| P1-3.2 检索失败计入 SLO 错误率 | retrieve.py 失败路径 try/except 调 engine.retrieve(); 异常时打 RETRIEVE_COUNT{status="failed"}; metric 失败不掩盖原异常 | 184 | +5 |
| P1-3.3 实现 /api/v1/admin/slo 端点 | 新增 `kb/observability/slo_metrics.py` (从 REGISTRY 读 counter/histogram); admin_slo 端点 (slos/active_alerts/error_budgets/prometheus_rules_yaml); ObservabilitySettings 子配置 | 684 | +18 |
| P1-3.4 删除 embedding-drift stub | admin.py:264-294 旧端点返回 mock 数据 → 删; .env.example 补 KB_OBSERVABILITY_* 3 行 | 44 | +3 |
| **合计** | | **+1076** | **+36** |

### A.7.3 评审夸大纠正 (本次新发现)

1. **3 级降级链 (engine.py:392) 不是 P1-3 修复, 是 I2-C2 已完成** — 评审 v1.0 当时未细分. 实际 P1-3 真实缺口集中在 "可观测" 侧 (slo.py 是死代码, PromQL 指标名错, status="failed" 不打点).
2. **P1-3 与 P1-1/P1-2 独立** — 评审把它们归一组, 实际 P1-3 是纯可观测问题 (后端 + 端点 + 配置), P1-1/P1-2 是核心功能问题.
3. **prometheus_client REGISTRY.collect() 直接读数** 比 HTTP 拉 /metrics 更安全 (无循环依赖, 进程内一致) — 这是调研时发现的最优解.

### A.7.4 维度重打分 (P1-3)

| 维度 | v1.1 评分 | v1.2 评分 | 变化原因 |
|---|---|---|---|
| 检索超时降级 | ★★ (3 级降级链已实现) | ★★★ | + SLO 告警接入生产 |
| SLO 可观测性 | ◐ (slo.py 死代码) | ★★ | 端点暴露 + 配置化 + PromQL 正确 |
| Prometheus 指标命名一致性 | ✗ (PromQL 错) | ★★ | availability/latency 两条分支都用真实指标名 |

### A.7.5 后续可继续 (P1-1 / P1-2 / P1-4 评审没动的部分)

- **P1-1**: SHADOW_DIVERGENCE 指标 0 调用; DriftMonitor threshold/window 未配置化; admin/shadow-compare 端点需手动传 chunk_ids
- **P1-2**: fail-open 是默认无开关; tier_quotas 不可 env 覆盖; fail-open 路径不打点
- **P1-4**: admin 一键回滚未做; content_unified_diff 是占位; rollback/takedown 不触发 ES 重新索引

---

文档版本: v1.1 (P0 落地基线)
维护者: SmartCS Team
落地 commit 区间: `12e08f4`..`bacfa94` (7 commits)
