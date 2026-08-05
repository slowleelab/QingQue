# Lumio 上线 Checklist (Sprint E)

> **目标**: 1000 RPS, P99 < 5s, 错误率 < 1%, 灰度发布零事故.
>
> **版本**: v1.0
> **日期**: 2026-08-04
> **Owner**: SRE + Bot/Assist Lead

---

## 1. 灰度发布策略

### 1.1 阶段划分

| 阶段 | 流量 | 持续时间 | 通过条件 | Owner |
|---|---|---|---|---|
| **Stage 0** (Canary) | 1% | 30min | 错误率 < 0.5%, P99 < 3s | Bot Lead |
| **Stage 1** | 10% | 2h | 错误率 < 1%, 客户投诉 < 5/小时 | SRE |
| **Stage 2** | 50% | 6h | 错误率 < 1%, 流量正常 | SRE + PM |
| **Stage 3** (Full) | 100% | 持续 | 全指标达标 | PM 决策 |

### 1.2 灰度路由

- 通过 Higress AI Gateway 按 `customer_id` hash 分桶
- 灰度期间: 旧版本 v1.5.0 / 新版本 v1.6.0
- 比例切换命令:
  ```bash
  # 切到 10%
  curl -X POST http://higress:8001/canary -d '{"new_version": "v1.6.0", "weight": 10}'
  # 全量
  curl -X POST http://higress:8001/canary -d '{"new_version": "v1.6.0", "weight": 100}'
  ```

### 1.3 观察指标

- **必看**:
  - 错误率 (5xx / 4xx)
  - P99 延迟
  - KV cache 命中率
  - LLM token 消耗 / 成本
  - 客户满意度 (差评率)
- **辅助**:
  - 工具调用成功率
  - 转人工率
  - 单轮对话长度
  - 显存 / GPU 利用率

---

## 2. 回滚预案

### 2.1 触发条件 (任一)

- 错误率 > 5% 持续 5min
- P99 > 10s 持续 5min
- KV cache 命中率跌至 < 30% (正常 80%+)
- 客户投诉 > 50/小时
- 安全事件 (PII 泄露 / 注入绕过)

### 2.2 回滚步骤

```bash
# Step 1: 立即切流 (1min)
curl -X POST http://higress:8001/canary -d '{"new_version": "v1.6.0", "weight": 0}'

# Step 2: 验证旧版本健康
curl http://lumio-bot:8000/api/health

# Step 3: 通知相关方
./scripts/notify_rollback.sh "lumio-bot v1.6.0 rollback due to high error rate"

# Step 4: 记录事件
./scripts/incident.sh start "lumio-bot v1.6.0 rollback"
```

### 2.3 数据回滚

- 会话状态: Redis 持久化,回滚不影响
- 客户画像: PostgreSQL,无破坏性变更,无需回滚
- 决策日志: 仅追加,无影响
- 集成测试结果: 需保留,作为下次发版阻断依据

---

## 3. 监控告警

### 3.1 P0 告警 (PagerDuty 寻呼, 5min 升级)

| 规则 | 阈值 | 动作 |
|---|---|---|
| 服务不可用 | 5xx > 50% 持续 1min | PagerDuty 寻呼值班 SRE |
| 错误率突增 | 错误率 > 5% 持续 5min | PagerDuty 寻呼值班 SRE |
| 关键路径超时 | LLM P99 > 30s 持续 5min | PagerDuty 寻呼值班 SRE |
| 安全事件 | PII 泄露 / 注入绕过 | 立即 PagerDuty + 安全团队 |

### 3.2 P1 告警 (邮件 + Slack #oncall, 工作时间响应)

| 规则 | 阈值 | 动作 |
|---|---|---|
| 性能降级 | P99 > 5s 持续 10min | Slack #oncall |
| 错误率上升 | 错误率 > 1% 持续 15min | Slack #oncall |
| 降级状态 | degradation_level 上升 | Slack #oncall |
| Token 消耗 | 单小时 cost > 阈值 (¥500) | Slack #oncall |

### 3.3 P2 告警 (Jira 工单, 24h SLA)

| 规则 | 阈值 | 动作 |
|---|---|---|
| KV cache 命中率下降 | 命中率 < 60% 持续 1h | Jira |
| Tool 失败率 | Tool 失败 > 5% 持续 1h | Jira |
| LLM 限流 | 限流命中 > 10% | Jira |
| 队列积压 | 队列长度 > 1000 | Jira |

### 3.4 仪表板

- **Grafana 主仪表板**: `lumio-prod-overview`
  - 流量 / 错误率 / 延迟 3 大面板
  - LLM 成本 / Token 消耗
  - KV cache 命中率
  - 工具调用分布
- **Grafana 安全仪表板**: `lumio-security`
  - 注入攻击拦截数
  - PII 检测数
  - 多租户异常
- **Grafana 业务仪表板**: `lumio-business`
  - 客户满意度
  - 转人工率
  - 单轮时长

---

## 4. 值班 oncall

### 4.1 排班

- **主值班**: 7x24, 1 人 / 班
- **副值班**: 7x24, 1 人 / 班 (升级用)
- **升级路径**: 主 → 副 (5min) → Bot Lead (15min) → EM (30min)

### 4.2 值班响应时间 SLA

| 级别 | 响应 | 处置 |
|---|---|---|
| P0 | 5min | 30min 内止损 |
| P1 | 30min | 4h 内处置 |
| P2 | 24h | 1 周内修复 |

### 4.3 值班手册 (Runbook)

- `docs/runbook/01_bot_500.md` — Bot 5xx 飙升
- `docs/runbook/02_kv_cache_low.md` — KV cache 命中率低
- `docs/runbook/03_llm_timeout.md` — LLM 超时
- `docs/runbook/04_redis_full.md` — Redis 内存满
- `docs/runbook/05_milvus_down.md` — Milvus 不可用
- `docs/runbook/06_es_down.md` — ES 不可用
- `docs/runbook/07_pii_leak.md` — PII 泄露应急
- `docs/runbook/08_rollback.md` — 回滚操作

---

## 5. 上线前必须完成

### 5.1 测试 (必过)

- [x] 单元测试覆盖率 >= 60% (branch coverage)
- [x] 集成测试 30 项 P0 全通过 (`tests/eval/integration_tests.py`)
- [x] 红队测试 0 漏报 (`services/bot/eval/red_team.py`)
- [x] 压测 1000 RPS P99 < 5s 错误率 < 1% (`scripts/load_test.py --ramp`)

### 5.2 配置 (必查)

- [x] `LUMIO_*` 环境变量已配置
- [x] PostgreSQL / Redis / ES / Milvus 连接 OK (`make verify`)
- [x] LLM API key 已配置 (主 + 备用)
- [x] Nacos Prompt 注册中心可达
- [x] 敏感词列表已加载
- [x] PII 实体白名单已配置

### 5.3 安全 (必查)

- [x] 30 项 P0 防护全在位 (KV cache / 压缩 / 防注入 / 隔离)
- [x] 多租户隔离测试通过
- [x] GDPR 删除 API 可调用
- [x] Token 预算熔断器就绪
- [x] 告警 P0/P1/P2 路由已配置

### 5.4 文档 (必交付)

- [x] README + AGENTS.md
- [x] API 文档 (OpenAPI)
- [x] 部署文档 (K8s manifest)
- [x] Runbook (8 篇)
- [x] 30 项 P0 验收报告

### 5.5 团队 (必就位)

- [x] Bot Lead 验收
- [x] SRE 值班就位
- [x] 安全团队 review 通过
- [x] 客服团队培训完成
- [x] 客户成功团队 (CSM) 通知到位

---

## 6. 上线 Day-1 / Day-7 / Day-30 检查

### 6.1 Day-1 (灰度期间)

- 流量分桶稳定 (Canary 1% / Stage 1 10%)
- 错误率 < 1%
- P99 延迟 < 5s
- 客户投诉 < 5/小时
- 转人工率无异常上升

### 6.2 Day-7 (50% 流量)

- 错误率 < 1%
- P99 延迟 < 3s
- KV cache 命中率 > 70%
- 客户满意度不下降
- 成本增长 < 30%

### 6.3 Day-30 (全量 1 个月)

- 错误率 < 0.5%
- 客户满意度提升
- 转人工率下降
- 成本符合预期
- 0 安全事件

---

## 7. 紧急联系方式

- **Bot Lead**: 见值班表
- **SRE 值班**: PagerDuty
- **安全团队**: security@lumio.com
- **PM**: pm@lumio.com
- **Nacos 维护**: nacos@lumio.com

---

## 8. 附录: 关键指标基线

| 指标 | 基线 (v1.5.0) | 目标 (v1.6.0) |
|---|---|---|
| 错误率 | 0.3% | < 0.5% |
| P99 延迟 | 4500ms | < 3000ms |
| P50 延迟 | 800ms | < 500ms |
| KV cache 命中率 | 0% | > 80% |
| 上下文压缩比 | 1.0x | 3-5x |
| 转人工率 | 12% | < 10% |
| 客户满意度 | 4.2/5 | > 4.3/5 |
| Token 成本 (单会话) | ¥0.05 | < ¥0.04 |
| 注入攻击拦截率 | N/A | 100% (24/24) |
| 多租户违规 | 0 | 0 |

---

**✅ 所有 30 项 P0 已交付, 集成测试 / 压测 / 上线 checklist 已就绪.**
