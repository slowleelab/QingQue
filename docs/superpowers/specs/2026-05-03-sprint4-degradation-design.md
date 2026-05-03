# Sprint 4 设计文档：LLM 集成 + 系统化降级策略

> 日期：2026-05-03
> 状态：已批准
> 范围：HealthMonitor 健康监控、DegradationManager 降级编排、ContentDegrader 内容降级链

## 1. 概述

Sprint 3 完成后，LLMClient 已具备基本调用能力和熔断器（CircuitBreaker），Agent 节点中散落了基础降级逻辑（LLM 失败 → 检索摘要 → 模板）。Sprint 4 将这些机制体系化为生产级的降级策略。

### 核心架构决策

| 决策项 | 选择 | 原因 |
|--------|------|------|
| 降级编排 | DegradationManager 集中编排 | 降级逻辑集中、行为一致、可测试 |
| 健康状态 | HealthMonitor（主动探测 + 被动熔断融合）| 单信号源不可靠，融合决策减少误判 |
| 模型降级 | 不做模型降级（14b→7b）| 同 Provider 内换模型对可用性问题无效，增加无效复杂度 |
| Provider 降级 | 不做多 Provider（当前仅 Ollama）| 架构预留扩展点，当前需求不涉及 |
| 降级级别 | 三级：NORMAL / DEGRADED / FALLBACK | 界限清晰，无模糊地带 |
| classify 降级 | 内聚在 IntentClassifier 内 | DegradationManager 只管 generate，不越界 |
| 探测恢复 | 指数退避 1s→30s，连续 2 次成功恢复 | 避免抖动，快速恢复 |

## 2. HealthMonitor + DegradationLevel

### 架构

```
┌─────────────────────────┐    ┌─────────────────────────┐
│    HealthProbe（主动）    │    │  CircuitBreaker（被动）    │
│  - 轻量 chat completion   │    │  - 连续失败计数/阈值        │
│  - 指数退避探测间隔        │    │  - 冷却期后半开试探         │
│  - 连续 2 次成功=恢复      │    │  - 成功后自动闭合          │
└───────────┬─────────────┘    └───────────┬─────────────┘
            │                              │
            └──────────┬───────────────────┘
                       │ 融合决策
              ┌────────▼────────┐
              │ DegradationLevel │
              │ NORMAL → DEGRADED│
              │ → FALLBACK       │
              └─────────────────┘
```

### 三级状态定义

| 级别 | 触发条件 | classify 行为 | generate 行为 |
|------|---------|--------------|---------------|
| NORMAL | breaker闭合 + 探测正常 | LLM classify + rule兜底 | LLM generate → 检索摘要 → 模板 |
| DEGRADED | breaker半开，探测偶发失败 | 规则 classify only | 检索摘要 → 模板（跳过 LLM）|
| FALLBACK | breaker打开 | 规则 classify only | 模板 → 硬编码兜底（跳过 LLM + 跳过检索）|

**触发规则**：
- NORMAL → DEGRADED：连续 2 次探测失败 或 breaker 打开
- DEGRADED → FALLBACK：breaker 打开 且 探测连续失败
- 恢复：breaker 闭合 + 连续 2 次探活成功 → 恢复到上一级

### 探测策略

- 初始间隔 1s，指数退避到最大 30s：`min(2^n, 30)`
- 探测内容：发送轻量 chat completion（max_tokens=5, content="ping"），验证推理链路完整
- 探测超时：5s
- 连续 2 次成功 → 间隔重置为 1s

### HealthMonitor 接口

```python
class HealthMonitor:
    level: DegradationLevel        # 当前降级级别
    is_llm_available: bool         # LLM 可用（NORMAL）
    is_degraded: bool              # 降级可用（NORMAL|DEGRADED）
    last_probe_latency_ms: float   # 最近探测延迟
    start() / stop()               # 后台探测任务
```

## 3. DegradationManager

generate 降级的统一编排入口。classify 降级不在此处，内聚在 `IntentClassifier.classify()` 内部。

```python
class DegradationManager:
    def __init__(
        self,
        llm_client: LLMClient,
        health_monitor: HealthMonitor,
        content_degrader: ContentDegrader,
    ): ...

    @property
    def level(self) -> DegradationLevel:       # 代理 health_monitor.level
        return self._health_monitor.level

    async def generate_with_fallback(
        self,
        system_prompt: str,
        user_input: str,
        context: str = "",
        intent_label: IntentLabel | None = None,
    ) -> GenerateResult:
        """
        NORMAL:     LLM generate(超时2s) → 失败→degrader.retrieval_summary
                    → context为空→degrader.get_template(intent)
        DEGRADED:   跳过LLM → degrader.retrieval_summary/context
                    → degrader.get_template(intent)
        FALLBACK:   跳过LLM → degrader.get_template(intent)
                    → 硬编码兜底
        """
```

### 关键设计点

- **不逐级重试**：根据 `health_monitor.level` 前置决定起始点
- **区分超时 vs 其他错误**：超时→跳过本次 LLM；breaker打开→不发起调用
- **无内部可变状态**：不引入 `_mark_llm_failed_this_turn()`，直接读 health_monitor.level + breaker.is_available 决策
- **每次调用打结构化日志**：`level_from`、`level_to`、`reason`、`latency_ms`、`session_id`

### GenerateResult

```python
@dataclass
class GenerateResult:
    content: str       # 生成/降级后的文本
    source: str        # "llm" | "retrieval" | "template" | "fallback"
```

## 4. ContentDegrader

```python
class ContentDegrader:
    def retrieval_summary(self, context: str, max_chars: int = 500) -> str:
        """检索上下文 → 可读摘要（智能截断，不依赖 LLM）"""
        # 按句号/换行智能截断，不截断中间

    def get_template(self, intent_label: IntentLabel | None) -> str:
        """按意图返回模板回复"""
        # knowledge 域分：账单/额度/积分/年费/FAQ 不同模板
        # business 域统一引导转人工
        # fallback 域通用模板

    def hardcoded_fallback(self) -> str:
        """最后保障：服务不可用提示 + 客服热线"""
```

### 模板覆盖

- knowledge 域按 IntentLabel 细分模板（账单查询、额度查询、积分兑换、年费、FAQ 各不同）
- business 域统一转人工引导
- fallback 域通用引导

## 5. LLMClient 修改

- `chat()` 方法新增 `timeout` 参数覆盖构造时的默认 `timeout`（OpenAI SDK `create()` 支持 per-request 覆盖）
- 新增 observability 字段：`total_calls`、`total_failures`、`avg_latency_ms`
- observability 采用日志埋点而非内存计数器（避免并发锁）

## 6. IntentClassifier 修改

classify() 方法内部集成 LLM 降级：

```python
async def classify(self, user_input: str) -> ClassifyResult:
    # Fast Path
    rule_result = self._rule.classify(user_input)
    if rule_result.primary_confidence >= self._fast_threshold:
        return rule_result, "rule"

    # Slow Path（NORMAL 才走，DEGRADED/FALLBACK 直接返回规则结果）
    if llm_client.breaker.is_available:
        try:
            llm_result = self._llm.classify(user_input, timeout=1.5)
            return llm_result, "llm"
        except Exception:
            ...
    return rule_result, "rule|fallback"
```

`HealthMonitor.level` 通过构造函数传入，不硬编码在 classify 方法内。

## 7. Agent 节点修改

各节点统一改为调用 `degradation_mgr.generate_with_fallback()`：

```python
async def knowledge_agent_node(state, *, degradation_mgr, ...):
    # 检索（FALLBACK 跳过）
    context = ""
    if degradation_mgr.level != DegradationLevel.FALLBACK:
        context = await do_retrieve(...)
        state["retrieval_context"] = context

    result = await degradation_mgr.generate_with_fallback(
        system_prompt=KNOWLEDGE_SYSTEM_PROMPT,
        user_input=user_input,
        context=context,
        intent_label=intent.primary_intent,
    )
    state["response"] = result.content
    state["response_source"] = result.source
    return state
```

AgentState 新增字段：`response_source: str` 追踪降级路径。

## 8. 配置变更

`LLMSettings` 新增：

```python
health_probe_interval_seconds: float = 1.0   # 初始探测间隔
health_probe_max_interval: float = 30.0      # 指数退避上限
health_probe_timeout: float = 5.0            # 探测超时
classify_timeout: float = 1.5                # 分类独立超时
generate_timeout: float = 2.0                # 生成独立超时
health_probe_fail_threshold: int = 2         # 连续失败降级阈值
health_probe_success_threshold: int = 2      # 连续成功恢复阈值
```

## 9. 测试计划

`tests/test_degradation.py` — 20 用例：

| 分类 | 用例 |
|------|------|
| HealthMonitor 状态机 | NORMAL→DEGRADED、DEGRADED→FALLBACK、恢复 |
| DegradationManager | NORMAL 正常生成、NORMAL LLM超时降级、DEGRADED 跳过LLM、FALLBACK 跳过检索和LLM |
| ContentDegrader | 检索摘要智能截断、各意图模板、硬编码兜底 |
| 集成 | Agent 节点 NORMAL/DEGRADED/FALLBACK 三级行为 |

## 10. 文件清单

```
新增:
  src/smartcs/services/common/degradation.py   # HealthMonitor + DegradationManager + ContentDegrader
  tests/test_degradation.py                    # 20 测试

修改:
  src/smartcs/services/common/llm.py           # per-call timeout, observability 日志
  src/smartcs/services/common/classifier.py    # classify() 内聚 LLM 降级
  src/smartcs/services/bot/agent.py            # AgentState + response_source, 调用 degradation_mgr
  src/smartcs/services/common/deps.py          # init/close/DI
  src/smartcs/main.py                          # bot_lifespan
  src/smartcs/shared/config.py                 # LLMSettings 扩展
  tests/test_agent.py                          # 适配新依赖
  tests/test_llm.py                            # 适配 per-call timeout
```
