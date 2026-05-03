# Sprint 4 Degradation Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build systematic LLM degradation: HealthMonitor (active probe + passive breaker fusion), DegradationManager (generate orchestration), ContentDegrader (retrieval summary → intent templates → hardcoded fallback).

**Architecture:** Three-level degradation (NORMAL → DEGRADED → FALLBACK) driven by HealthMonitor fusing active probe + CircuitBreaker state. DegradationManager decides per-call starting level; ContentDegrader provides content fallback chain. classify degradation stays inside IntentClassifier.

**Tech Stack:** Python 3.11+, asyncio, OpenAI SDK (Ollama), existing LLMClient/CircuitBreaker

**Spec:** `docs/superpowers/specs/2026-05-03-sprint4-degradation-design.md`

---

### Task 1: Add DegradationLevel enum and LLMSettings fields

**Files:**
- Modify: `src/smartcs/shared/models.py` — add `DegradationLevel` enum
- Modify: `src/smartcs/shared/config.py` — add health probe fields to `LLMSettings`

- [ ] **Step 1: Add DegradationLevel enum to models.py**

Add after `TransferTriggerLevel` (line ~69):

```python
class DegradationLevel(str, Enum):
    """LLM 降级级别"""
    NORMAL = "normal"        # LLM 可用，正常调用
    DEGRADED = "degraded"    # LLM 降级，跳过 LLM 用检索摘要
    FALLBACK = "fallback"    # LLM 不可用，跳过检索直接用模板
```

- [ ] **Step 2: Add health probe config fields to LLMSettings in config.py**

Add to `LLMSettings` class (after `timeout_seconds`, line ~110):

```python
    # 健康探测
    health_probe_interval_seconds: float = 1.0   # 初始探测间隔
    health_probe_max_interval: float = 30.0      # 指数退避上限
    health_probe_timeout: float = 5.0            # 探测超时
    health_probe_fail_threshold: int = 2         # 连续失败降级阈值
    health_probe_success_threshold: int = 2      # 连续成功恢复阈值
    # 各类独立超时
    classify_timeout: float = 1.5                # 分类独立超时
    generate_timeout: float = 2.0                # 生成独立超时
```

- [ ] **Step 3: Run tests to verify no regression**

```bash
cd /Users/qiangli/CodeBuddy/agent_project && poetry run pytest tests/ -x -q
```

Expected: All existing tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/smartcs/shared/models.py src/smartcs/shared/config.py
git commit -m "feat: add DegradationLevel enum and LLM health probe config fields"
```

---

### Task 2: Write tests for DegradationLevel + HealthMonitor state machine

**Files:**
- Create: `tests/test_degradation.py`

- [ ] **Step 1: Write test file with DegradationLevel enum tests and HealthMonitor state machine tests**

```python
"""降级策略单元测试"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from smartcs.shared.models import DegradationLevel
from smartcs.services.common.degradation import (
    ContentDegrader,
    DegradationManager,
    GenerateResult,
    HealthMonitor,
)
from smartcs.shared.models import IntentLabel


# ── DegradationLevel ──


def test_degradation_level_values():
    """验证三级值和顺序"""
    assert DegradationLevel.NORMAL.value == "normal"
    assert DegradationLevel.DEGRADED.value == "degraded"
    assert DegradationLevel.FALLBACK.value == "fallback"
    # NORMAL > DEGRADED > FALLBACK (按严重程度排序)
    levels = list(DegradationLevel)
    assert levels == [
        DegradationLevel.NORMAL,
        DegradationLevel.DEGRADED,
        DegradationLevel.FALLBACK,
    ]


# ── HealthMonitor state machine ──


class MockLLMClient:
    """模拟 LLMClient — 仅提供 chat 方法用于探测"""
    def __init__(self, fail_count: int = 0):
        self.call_count = 0
        self.fail_count = fail_count

    async def chat(self, messages, **kwargs):
        self.call_count += 1
        if self.call_count <= self.fail_count:
            raise Exception("模拟 LLM 不可用")
        return "pong"


class MockBreaker:
    """模拟 CircuitBreaker"""
    def __init__(self, is_available: bool = True):
        self._available = is_available

    @property
    def is_available(self) -> bool:
        return self._available


@pytest.mark.asyncio
async def test_health_monitor_initial_state():
    """初始状态为 NORMAL"""
    llm = MockLLMClient()
    breaker = MockBreaker()
    monitor = HealthMonitor(
        llm_client=llm,
        breaker=breaker,
        probe_interval=0.1,
        probe_timeout=5.0,
        fail_threshold=2,
        success_threshold=2,
    )
    assert monitor.level == DegradationLevel.NORMAL
    assert monitor.is_llm_available is True


@pytest.mark.asyncio
async def test_health_monitor_probe_success():
    """探测成功保持 NORMAL"""
    llm = MockLLMClient()
    breaker = MockBreaker()
    monitor = HealthMonitor(
        llm_client=llm,
        breaker=breaker,
        probe_interval=0.1,
        probe_timeout=5.0,
        fail_threshold=2,
        success_threshold=2,
    )
    await monitor._probe_once()
    assert monitor.level == DegradationLevel.NORMAL


@pytest.mark.asyncio
async def test_health_monitor_degraded_after_failures():
    """连续探测失败 2 次 → DEGRADED"""
    llm = MockLLMClient(fail_count=10)
    breaker = MockBreaker()
    monitor = HealthMonitor(
        llm_client=llm,
        breaker=breaker,
        probe_interval=0.1,
        probe_timeout=5.0,
        fail_threshold=2,
        success_threshold=2,
    )
    # 连续 2 次失败
    await monitor._probe_once()
    assert monitor.level == DegradationLevel.NORMAL  # 第 1 次失败不降级
    await monitor._probe_once()
    assert monitor.level == DegradationLevel.DEGRADED  # 第 2 次失败降级


@pytest.mark.asyncio
async def test_health_monitor_fallback_when_breaker_open():
    """breaker 打开 → 直接 FALLBACK"""
    llm = MockLLMClient()
    breaker = MockBreaker(is_available=False)
    monitor = HealthMonitor(
        llm_client=llm,
        breaker=breaker,
        probe_interval=0.1,
        probe_timeout=5.0,
        fail_threshold=2,
        success_threshold=2,
    )
    # breaker 打开 + 初始即为 FALLBACK
    assert monitor.level == DegradationLevel.FALLBACK


@pytest.mark.asyncio
async def test_health_monitor_recovery():
    """DEGRADED → 连续 2 次探测成功 → NORMAL"""
    llm = MockLLMClient(fail_count=2)
    breaker = MockBreaker()
    monitor = HealthMonitor(
        llm_client=llm,
        breaker=breaker,
        probe_interval=0.1,
        probe_timeout=5.0,
        fail_threshold=2,
        success_threshold=2,
    )
    # 先降级到 DEGRADED
    await monitor._probe_once()
    await monitor._probe_once()
    assert monitor.level == DegradationLevel.DEGRADED

    # 后两次成功
    await monitor._probe_once()
    assert monitor.level == DegradationLevel.DEGRADED  # 第 1 次成功不恢复
    await monitor._probe_once()
    assert monitor.level == DegradationLevel.NORMAL  # 第 2 次成功恢复


@pytest.mark.asyncio
async def test_health_monitor_backoff():
    """探测间隔指数退避"""
    monitor = HealthMonitor(
        llm_client=MockLLMClient(),
        breaker=MockBreaker(),
        probe_interval=1.0,
        probe_timeout=5.0,
        fail_threshold=2,
        success_threshold=2,
    )
    assert monitor._current_interval == 1.0
    monitor._failures = 3
    monitor._update_interval()
    assert monitor._current_interval == 8.0  # min(2^3, 30) = 8
    monitor._failures = 10
    monitor._update_interval()
    assert monitor._current_interval == 30.0  # capped at max


# ── ContentDegrader ──


def test_retrieval_summary_with_context():
    """有上下文时生成摘要"""
    degrader = ContentDegrader()
    context = "账单查询方法：登录APP查看账单详情。\n\n还款方式：支持自动还款和手动还款。"
    result = degrader.retrieval_summary(context)
    assert "账单查询方法" in result
    assert "还有" in result  # 有第二条


def test_retrieval_summary_empty():
    """无上下文时返回空"""
    degrader = ContentDegrader()
    assert degrader.retrieval_summary("") == ""


def test_retrieval_summary_single_chunk():
    """单条上下文"""
    degrader = ContentDegrader()
    result = degrader.retrieval_summary("信用卡额度调整规则：根据用卡情况每半年评估一次。")
    assert "信用卡额度调整规则" in result
    assert "还有" not in result


def test_get_template_knowledge_intents():
    """knowledge 域各意图有不同模板"""
    degrader = ContentDegrader()
    bill_template = degrader.get_template(IntentLabel.BILL_QUERY)
    reward_template = degrader.get_template(IntentLabel.REWARD_QUERY)
    assert bill_template != reward_template
    assert "账单" in bill_template


def test_get_template_business_intents():
    """business 域统一引导转人工"""
    degrader = ContentDegrader()
    loss_template = degrader.get_template(IntentLabel.CARD_LOSS)
    complaint_template = degrader.get_template(IntentLabel.COMPLAINT)
    assert "转人工" in loss_template
    assert "转人工" in complaint_template


def test_get_template_fallback():
    """fallback 域通用引导"""
    degrader = ContentDegrader()
    template = degrader.get_template(IntentLabel.CHITCHAT)
    assert "抱歉" in template or "问题" in template


def test_hardcoded_fallback():
    """最后保障"""
    degrader = ContentDegrader()
    result = degrader.hardcoded_fallback()
    assert "抱歉" in result or "服务" in result
    assert len(result) > 0


# ── DegradationManager ──


@pytest.fixture
def mock_llm_client():
    """正常工作的 LLM 客户端"""
    client = MagicMock()
    client.breaker = MagicMock()
    client.breaker.is_available = True
    return client


@pytest.fixture
def mock_health_monitor():
    """NORMAL 状态监控器"""
    monitor = MagicMock()
    monitor.level = DegradationLevel.NORMAL
    monitor.is_llm_available = True
    return monitor


@pytest.fixture
def mock_content_degrader():
    """内容降级器"""
    degrader = MagicMock(spec=ContentDegrader)
    degrader.retrieval_summary.return_value = "检索摘要"
    degrader.get_template.return_value = "模板回复"
    degrader.hardcoded_fallback.return_value = "服务不可用"
    return degrader


@pytest.mark.asyncio
async def test_generate_normal_success(mock_llm_client, mock_health_monitor, mock_content_degrader):
    """NORMAL 状态 LLM 正常生成"""
    mock_llm_client.generate = AsyncMock(return_value="LLM 生成的回复")

    mgr = DegradationManager(mock_llm_client, mock_health_monitor, mock_content_degrader)
    result = await mgr.generate_with_fallback(
        system_prompt="你是客服",
        user_input="账单怎么查",
        context="账单查询：登录APP...",
    )
    assert result.content == "LLM 生成的回复"
    assert result.source == "llm"


@pytest.mark.asyncio
async def test_generate_normal_llm_timeout_falls_back_to_retrieval(
    mock_llm_client, mock_health_monitor, mock_content_degrader
):
    """NORMAL 状态 LLM 超时 → 检索摘要降级"""
    mock_llm_client.generate = AsyncMock(side_effect=Exception("超时"))

    mgr = DegradationManager(mock_llm_client, mock_health_monitor, mock_content_degrader)
    result = await mgr.generate_with_fallback(
        system_prompt="你是客服",
        user_input="账单怎么查",
        context="账单查询相关信息...",
    )
    assert result.source == "retrieval"
    mock_content_degrader.retrieval_summary.assert_called_once()


@pytest.mark.asyncio
async def test_generate_normal_no_context_falls_back_to_template(
    mock_llm_client, mock_health_monitor, mock_content_degrader
):
    """NORMAL 状态 LLM 失败 + 无检索上下文 → 模板"""
    mock_llm_client.generate = AsyncMock(side_effect=Exception("超时"))

    mgr = DegradationManager(mock_llm_client, mock_health_monitor, mock_content_degrader)
    result = await mgr.generate_with_fallback(
        system_prompt="你是客服",
        user_input="你好",
        context="",
        intent_label=IntentLabel.CHITCHAT,
    )
    assert result.source == "template"
    mock_content_degrader.get_template.assert_called_once_with(IntentLabel.CHITCHAT)


@pytest.mark.asyncio
async def test_generate_degraded_skips_llm(
    mock_llm_client, mock_health_monitor, mock_content_degrader
):
    """DEGRADED 状态跳过 LLM 直接用检索摘要"""
    mock_health_monitor.level = DegradationLevel.DEGRADED
    mock_llm_client.generate = AsyncMock()

    mgr = DegradationManager(mock_llm_client, mock_health_monitor, mock_content_degrader)
    result = await mgr.generate_with_fallback(
        system_prompt="你是客服",
        user_input="账单怎么查",
        context="账单查询相关信息...",
    )
    assert result.source == "retrieval"
    mock_llm_client.generate.assert_not_called()


@pytest.mark.asyncio
async def test_generate_fallback_skips_llm_and_retrieval(
    mock_llm_client, mock_health_monitor, mock_content_degrader
):
    """FALLBACK 状态跳过 LLM + 检索，直接用模板"""
    mock_health_monitor.level = DegradationLevel.FALLBACK
    mock_llm_client.generate = AsyncMock()

    mgr = DegradationManager(mock_llm_client, mock_health_monitor, mock_content_degrader)
    result = await mgr.generate_with_fallback(
        system_prompt="你是客服",
        user_input="账单怎么查",
        context="",
        intent_label=IntentLabel.BILL_QUERY,
    )
    assert result.source == "template"
    mock_llm_client.generate.assert_not_called()
    mock_content_degrader.get_template.assert_called_once_with(IntentLabel.BILL_QUERY)


@pytest.mark.asyncio
async def test_degradation_manager_level_property(mock_llm_client, mock_health_monitor, mock_content_degrader):
    """level property 代理 health_monitor.level"""
    mgr = DegradationManager(mock_llm_client, mock_health_monitor, mock_content_degrader)
    assert mgr.level == DegradationLevel.NORMAL
```

- [ ] **Step 2: Create minimal stub in degradation.py so tests can be imported**

Create `src/smartcs/services/common/degradation.py` with stubs:

```python
"""降级策略管理模块"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from smartcs.shared.models import DegradationLevel, IntentLabel

logger = logging.getLogger(__name__)


@dataclass
class GenerateResult:
    content: str
    source: str  # "llm" | "retrieval" | "template" | "fallback"


class HealthMonitor:
    def __init__(self, llm_client, breaker, probe_interval=1.0, probe_max_interval=30.0,
                 probe_timeout=5.0, fail_threshold=2, success_threshold=2):
        self._llm = llm_client
        self._breaker = breaker
        self._probe_interval = probe_interval
        self._max_interval = probe_max_interval
        self._probe_timeout = probe_timeout
        self._fail_threshold = fail_threshold
        self._success_threshold = success_threshold
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._current_interval = probe_interval
        self._level = DegradationLevel.FALLBACK if not breaker.is_available else DegradationLevel.NORMAL
        self._task: asyncio.Task | None = None

    @property
    def level(self) -> DegradationLevel:
        return self._level

    @property
    def is_llm_available(self) -> bool:
        return self._level == DegradationLevel.NORMAL

    async def start(self) -> None:
        """启动后台探测任务"""
        if self._task is None:
            self._task = asyncio.create_task(self._probe_loop())

    async def stop(self) -> None:
        """停止后台探测任务"""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _probe_loop(self) -> None:
        """后台探测循环"""
        while True:
            await asyncio.sleep(self._current_interval)
            await self._probe_once()

    async def _probe_once(self) -> None:
        try:
            await self._llm.chat(
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                timeout=self._probe_timeout,
            )
            self._consecutive_successes += 1
            self._consecutive_failures = 0
        except Exception:
            self._consecutive_failures += 1
            self._consecutive_successes = 0
        self._recompute_level()

    def _update_interval(self) -> None:
        n = self._consecutive_failures
        if n == 0:
            self._current_interval = self._probe_interval
        else:
            self._current_interval = min(2.0 ** n, self._max_interval)

    def _recompute_level(self) -> None:
        if self._consecutive_failures >= self._fail_threshold:
            self._level = DegradationLevel.DEGRADED
        elif self._consecutive_successes >= self._success_threshold:
            self._level = DegradationLevel.NORMAL
        self._update_interval()


class ContentDegrader:
    _TEMPLATES: dict[IntentLabel, str] = {}

    def retrieval_summary(self, context: str, max_chars: int = 500) -> str:
        chunks = [c.strip() for c in context.split("\n\n") if c.strip()]
        if not chunks:
            return ""
        first = chunks[0]
        if len(first) > max_chars:
            last_period = first.rfind("。", 0, max_chars)
            last_newline = first.rfind("\n", 0, max_chars)
            cut = max(last_period, last_newline, max_chars - 10)
            first = first[:cut + 1]
        summary = f"根据相关信息：{first}"
        if len(chunks) > 1:
            summary += f"\n\n还有 {len(chunks) - 1} 条相关内容，如需了解请详细描述您的问题。"
        return summary

    def get_template(self, intent_label: IntentLabel | None) -> str:
        return self._TEMPLATES.get(intent_label, self.hardcoded_fallback()) if intent_label else self.hardcoded_fallback()

    def hardcoded_fallback(self) -> str:
        return "抱歉，服务暂时不可用，请稍后再试或拨打客服热线。"


class DegradationManager:
    def __init__(self, llm_client, health_monitor, content_degrader):
        self._llm = llm_client
        self._health_monitor = health_monitor
        self._degrader = content_degrader

    @property
    def level(self) -> DegradationLevel:
        return self._health_monitor.level

    async def generate_with_fallback(self, system_prompt, user_input, context="",
                                     intent_label=None):
        level = self._health_monitor.level
        if level == DegradationLevel.NORMAL and self._llm.breaker.is_available:
            try:
                resp = await self._llm.generate(
                    system_prompt=system_prompt,
                    user_input=user_input,
                    context=context,
                )
                return GenerateResult(content=resp, source="llm")
            except Exception:
                pass
        if context:
            return GenerateResult(
                content=self._degrader.retrieval_summary(context),
                source="retrieval",
            )
        return GenerateResult(
            content=self._degrader.get_template(intent_label),
            source="template",
        )
```

- [ ] **Step 3: Run tests to verify they pass**

```bash
cd /Users/qiangli/CodeBuddy/agent_project && poetry run pytest tests/test_degradation.py -v
```

Expected: 17 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/test_degradation.py src/smartcs/services/common/degradation.py
git commit -m "feat: add degradation module with HealthMonitor, DegradationManager, ContentDegrader"
```

---

### Task 3: Enhance LLMClient with per-call timeout and observability

**Files:**
- Modify: `src/smartcs/services/common/llm.py` — `chat()` per-call timeout, observability logging

- [ ] **Step 1: Update `chat()` method to accept optional `timeout` parameter**

In `src/smartcs/services/common/llm.py`, modify the `chat()` method signature and call:

```python
async def chat(
    self,
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    json_mode: bool = False,
    timeout: float | None = None,  # NEW: per-call timeout override
) -> str:
```

In the `create()` call within the retry loop, add timeout override:

```python
kwargs: dict[str, Any] = {
    "model": model or self._settings.primary_model,
    "messages": messages,
    "temperature": temperature if temperature is not None else self._settings.temperature,
    "max_tokens": max_tokens or self._settings.max_tokens,
}
if timeout is not None:
    kwargs["timeout"] = timeout  # OpenAI SDK per-request override
```

The rest of the method stays unchanged (retry, breaker, etc.).

- [ ] **Step 2: Add observability logging to `chat()`**

Add before `return content` in the success path:

```python
# Observability: log call metrics
elapsed = time.monotonic() - _start
logger.debug(
    "LLM call succeeded: model=%s, latency_ms=%d, tokens=%d",
    kwargs["model"],
    int(elapsed * 1000),
    response.usage.total_tokens if response.usage else 0,
)
```

Add `_start = time.monotonic()` before the retry loop.

- [ ] **Step 3: Run existing LLM tests to verify no regression**

```bash
cd /Users/qiangli/CodeBuddy/agent_project && poetry run pytest tests/test_llm.py -v
```

Expected: All 9 LLM tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/smartcs/services/common/llm.py
git commit -m "feat: add per-call timeout and observability logging to LLMClient"
```

---

### Task 4: Integrate LLM degradation into IntentClassifier

**Files:**
- Modify: `src/smartcs/services/common/classifier.py` — `classify()` uses `llm_client.breaker` for degrade decision

- [ ] **Step 1: Update `IntentClassifier.classify()` to check breaker before LLM call**

In `src/smartcs/services/common/classifier.py`, modify the Slow Path section of `IntentClassifier.classify()` (lines ~308-323):

```python
    # Slow Path
    if self._llm is None:
        logger.debug("Slow Path 不可用，使用 Fast Path 低置信度结果")
        return rule_result, [], SentimentLabel.NEUTRAL, "fallback"

    # 熔断器打开 → 跳过 LLM
    if not self._llm._breaker.is_available:
        logger.debug("LLM 熔断器打开，跳过 Slow Path")
        return rule_result, [], SentimentLabel.NEUTRAL, "fallback"

    logger.debug(
        "Fast Path 置信度不足 (%.2f < %.2f)，进入 Slow Path",
        rule_result.primary_confidence,
        self._threshold,
    )

    try:
        llm_result, entities, sentiment = await self._llm.classify(text)
    except Exception:
        logger.warning("LLM 分类调用失败，使用规则结果兜底")
        return rule_result, [], SentimentLabel.NEUTRAL, "fallback"

    # LLM 结果置信度也很低时，标记来源为 fallback
    source = "llm" if llm_result.primary_confidence >= 0.3 else "fallback"
    return llm_result, entities, sentiment, source
```

(The existing LLMClassifier already has its own try/except returning fallback.)
The key change: IntentClassifier now checks `self._llm._breaker.is_available` before attempting Slow Path, and wraps the LLM call in try/except to catch any errors including the breaker check inside `llm_client.classify()`.

- [ ] **Step 2: Update LLMClient.classify() to accept per-call timeout**

In `classify()` method (line ~189), add `timeout` parameter and pass it through to `chat_json()`:

```python
async def classify(
    self,
    system_prompt: str,
    user_input: str,
    *,
    model: str | None = None,
    timeout: float | None = None,  # NEW
) -> dict[str, Any]:
```

Pass `timeout` to `self.chat_json(...)`.

- [ ] **Step 3: Run classifier tests to verify**

```bash
cd /Users/qiangli/CodeBuddy/agent_project && poetry run pytest tests/test_classifier.py -v
```

Expected: All 15 classifier tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/smartcs/services/common/classifier.py src/smartcs/services/common/llm.py
git commit -m "feat: integrate breaker check into IntentClassifier Slow Path"
```

---

### Task 5: Add init/close/DI for degradation components in deps.py

**Files:**
- Modify: `src/smartcs/services/common/deps.py`

- [ ] **Step 1: Add import for degradation components**

At the top of deps.py, add:

```python
from smartcs.services.common.degradation import (
    ContentDegrader,
    DegradationManager,
    HealthMonitor,
)
```

- [ ] **Step 2: Add init/close functions for HealthMonitor**

After the `init_llm` / `close_llm` section (~line 245):

```python
# ── 健康监控 ──


async def init_health_monitor(app) -> None:
    """初始化健康监控器，存储到 app.state"""
    llm_client = app.state.llm_client
    breaker = app.state.llm_breaker
    settings = get_settings()
    monitor = HealthMonitor(
        llm_client=llm_client,
        breaker=breaker,
        probe_interval=settings.llm.health_probe_interval_seconds,
        probe_max_interval=settings.llm.health_probe_max_interval,
        probe_timeout=settings.llm.health_probe_timeout,
        fail_threshold=settings.llm.health_probe_fail_threshold,
        success_threshold=settings.llm.health_probe_success_threshold,
    )
    await monitor.start()
    app.state.health_monitor = monitor


async def close_health_monitor(app) -> None:
    """关闭健康监控器"""
    monitor = getattr(app.state, "health_monitor", None)
    if monitor:
        await monitor.stop()
        app.state.health_monitor = None


def get_health_monitor(request: Request) -> HealthMonitor:
    """获取健康监控器（FastAPI 依赖注入）"""
    return request.app.state.health_monitor
```

- [ ] **Step 3: Add init/close functions for DegradationManager**

After health monitor section:

```python
# ── 降级管理 ──


async def init_degradation_manager(app) -> None:
    """初始化降级管理器，存储到 app.state"""
    llm_client = app.state.llm_client
    health_monitor = app.state.health_monitor
    content_degrader = ContentDegrader()
    mgr = DegradationManager(
        llm_client=llm_client,
        health_monitor=health_monitor,
        content_degrader=content_degrader,
    )
    app.state.degradation_manager = mgr


async def close_degradation_manager(app) -> None:
    """关闭降级管理器（无需特殊清理）"""
    app.state.degradation_manager = None


def get_degradation_manager(request: Request) -> DegradationManager:
    """获取降级管理器（FastAPI 依赖注入）"""
    return request.app.state.degradation_manager
```

- [ ] **Step 4: Add type aliases at the bottom**

After `AgentDep` (line ~365):

```python
HealthMonitorDep = Annotated[HealthMonitor, Depends(get_health_monitor)]
DegradationManagerDep = Annotated[DegradationManager, Depends(get_degradation_manager)]
```

- [ ] **Step 5: Run existing tests to verify no import errors**

```bash
cd /Users/qiangli/CodeBuddy/agent_project && poetry run pytest tests/test_agent.py -v
```

Expected: No import errors.

- [ ] **Step 6: Commit**

```bash
git add src/smartcs/services/common/deps.py
git commit -m "feat: add HealthMonitor and DegradationManager to DI container"
```

---

### Task 6: Update bot lifespan in main.py

**Files:**
- Modify: `src/smartcs/main.py`

- [ ] **Step 1: Import new init/close functions**

Add to imports in main.py:

```python
from smartcs.services.common.deps import (
    # ... existing imports ...
    close_health_monitor,
    close_degradation_manager,
    init_health_monitor,
    init_degradation_manager,
)
```

- [ ] **Step 2: Insert init/close calls in bot_lifespan**

In `bot_lifespan()`, after `init_agent(app)`:

```python
    await init_agent(app)
    await init_health_monitor(app)       # NEW: after agent, starts background probe
    await init_degradation_manager(app)  # NEW: after health monitor
```

In shutdown, before `close_agent(app)`:

```python
    await close_degradation_manager(app)  # NEW
    await close_health_monitor(app)       # NEW: stops background probe
    await close_agent(app)
```

- [ ] **Step 3: Verify app imports cleanly**

```bash
cd /Users/qiangli/CodeBuddy/agent_project && poetry run python -c "from smartcs.main import bot_app; print('OK')"
```

Expected: "OK", no errors.

- [ ] **Step 4: Commit**

```bash
git add src/smartcs/main.py
git commit -m "feat: initialize HealthMonitor and DegradationManager in bot lifespan"
```

---

### Task 7: Update Agent nodes to use DegradationManager

**Files:**
- Modify: `src/smartcs/services/bot/agent.py`

- [ ] **Step 1: Add `response_source` to AgentState**

In `AgentState` class, add field:

```python
    response_source: str  # "llm" | "retrieval" | "template" | "fallback"
```

And in `_initial_state()`, add:

```python
    response_source="",
```

- [ ] **Step 2: Import DegradationManager**

Add at top:

```python
from smartcs.services.common.degradation import DegradationManager, GenerateResult
```

- [ ] **Step 3: Update `knowledge_agent_node` to use DegradationManager**

Replace the LLM generation + degradation block with:

```python
async def knowledge_agent_node(
    state: AgentState,
    *,
    degradation_mgr: DegradationManager,
    es_client: AsyncElasticsearch | None,
    milvus_collection: Collection | None,
    embedding_breaker: EmbeddingCircuitBreaker | None,
) -> AgentState:
    from smartcs.services.common.retrieval import retrieve as do_retrieve
    from smartcs.shared.models import DegradationLevel

    settings = get_settings()
    user_input = state["user_input"]
    intent = state.get("intent")

    # 检索（FALLBACK 跳过）
    context = ""
    if degradation_mgr.level != DegradationLevel.FALLBACK:
        embedding_provider = embedding_breaker.provider if embedding_breaker and embedding_breaker.is_available else None
        retrieve_response: RetrieveResponse = await do_retrieve(
            request=RetrieveRequest(query=user_input, top_k=settings.rag.top_k, rerank=False),
            es_client=es_client,
            milvus_collection=milvus_collection,
            embedding_provider=embedding_provider,
            reranker=None,
        )
        if retrieve_response.results:
            context_parts = [f"[{i+1}] {r.content}" for i, r in enumerate(retrieve_response.results)]
            context = "\n\n".join(context_parts)
            state["retrieval_context"] = context
        else:
            state["retrieval_context"] = ""
    else:
        state["retrieval_context"] = ""

    # 通过降级管理器生成
    result = await degradation_mgr.generate_with_fallback(
        system_prompt=KNOWLEDGE_SYSTEM_PROMPT,
        user_input=user_input,
        context=context,
        intent_label=intent.primary_intent if intent else None,
    )
    state["response"] = result.content
    state["response_source"] = result.source
    return state
```

- [ ] **Step 4: Update `business_agent_node` to use DegradationManager**

```python
async def business_agent_node(
    state: AgentState,
    *,
    degradation_mgr: DegradationManager,
) -> AgentState:
    user_input = state["user_input"]
    intent = state.get("intent")

    # 挂失/投诉/转人工 → 直接触发转人工
    if intent and intent.primary_intent in (IntentLabel.CARD_LOSS, IntentLabel.COMPLAINT, IntentLabel.TRANSFER_AGENT):
        reason = {
            IntentLabel.CARD_LOSS: "挂失业务",
            IntentLabel.COMPLAINT: "投诉处理",
            IntentLabel.TRANSFER_AGENT: "客户主动请求",
        }.get(intent.primary_intent, "业务办理")
        state["should_transfer"] = True
        state["transfer_reason"] = reason
        state["response"] = BUSINESS_TRANSFER_TEMPLATE.format(reason=reason)
        state["response_source"] = "template"
        return state

    # 其他业务咨询通过降级管理器生成
    result = await degradation_mgr.generate_with_fallback(
        system_prompt=BUSINESS_SYSTEM_PROMPT,
        user_input=user_input,
        context="",
        intent_label=intent.primary_intent if intent else None,
    )
    state["response"] = result.content
    state["response_source"] = result.source
    return state
```

- [ ] **Step 5: Update `fallback_agent_node` to use DegradationManager**

```python
async def fallback_agent_node(
    state: AgentState,
    *,
    degradation_mgr: DegradationManager,
) -> AgentState:
    user_input = state["user_input"]

    # 快速匹配闲聊模式（不调 LLM）
    if _is_greeting(user_input):
        state["response"] = GREETING_RESPONSE
        state["response_source"] = "template"
        return state

    if _is_farewell(user_input):
        state["response"] = FAREWELL_RESPONSE
        state["response_source"] = "template"
        return state

    # 通过降级管理器生成
    result = await degradation_mgr.generate_with_fallback(
        system_prompt=FALLBACK_SYSTEM_PROMPT,
        user_input=user_input,
        context="",
        intent_label=IntentLabel.CHITCHAT,
    )
    state["response"] = result.content
    state["response_source"] = result.source
    return state
```

- [ ] **Step 6: Update SmartCSAgent constructor to receive and pass DegradationManager**

```python
class SmartCSAgent:
    def __init__(
        self,
        classifier: IntentClassifier,
        degradation_mgr: DegradationManager,  # replaces llm_client
        transfer_checker: TransferChecker,
        session_manager: SessionManager,
        es_client: AsyncElasticsearch | None = None,
        milvus_collection: Collection | None = None,
        embedding_breaker: EmbeddingCircuitBreaker | None = None,
    ) -> None:
        self._classifier = classifier
        self._degradation_mgr = degradation_mgr
        self._transfer_checker = transfer_checker
        self._session_manager = session_manager
        self._es_client = es_client
        self._milvus_collection = milvus_collection
        self._embedding_breaker = embedding_breaker
        self._graph = self._build_graph()
```

Update node binding methods:

```python
async def _knowledge_agent(self, state: AgentState) -> AgentState:
    return await knowledge_agent_node(
        state,
        degradation_mgr=self._degradation_mgr,
        es_client=self._es_client,
        milvus_collection=self._milvus_collection,
        embedding_breaker=self._embedding_breaker,
    )

async def _business_agent(self, state: AgentState) -> AgentState:
    return await business_agent_node(state, degradation_mgr=self._degradation_mgr)

async def _fallback_agent(self, state: AgentState) -> AgentState:
    return await fallback_agent_node(state, degradation_mgr=self._degradation_mgr)
```

- [ ] **Step 7: Update `init_agent` in deps.py to pass DegradationManager**

In `init_agent()`:

```python
async def init_agent(app) -> None:
    from smartcs.services.bot.agent import SmartCSAgent

    classifier = app.state.classifier
    degradation_mgr = app.state.degradation_manager  # NEW
    transfer_checker = app.state.transfer_checker
    session_manager = app.state.session_manager

    agent = SmartCSAgent(
        classifier=classifier,
        degradation_mgr=degradation_mgr,  # NEW
        transfer_checker=transfer_checker,
        session_manager=session_manager,
        es_client=getattr(app.state, "es_client", None),
        milvus_collection=getattr(app.state, "milvus_collection", None),
        embedding_breaker=getattr(app.state, "embedding_breaker", None),
    )
    app.state.agent = agent
    _logger.info("对话 Agent 初始化完成")
```

- [ ] **Step 8: Update chatbot router to include response_source in response**

In `router.py`'s `/chat` endpoint:

```python
    return ChatResponse(
        session_id=session.session_id,
        reply=result.get("response", "抱歉，我暂时无法处理您的请求。"),
        intent=intent.primary_intent if intent else None,
        confidence=intent.primary_confidence if intent else 0.0,
        source=result.get("response_source", "fallback"),  # was hardcoded logic
        is_transfer=is_transfer,
    )
```

- [ ] **Step 9: Run agent tests to verify**

```bash
cd /Users/qiangli/CodeBuddy/agent_project && poetry run pytest tests/test_agent.py -v
```

Expected: Tests pass with degradation_mgr mock.

- [ ] **Step 10: Commit**

```bash
git add src/smartcs/services/bot/agent.py src/smartcs/services/common/deps.py src/smartcs/services/bot/router.py
git commit -m "feat: integrate DegradationManager into Agent nodes"
```

---

### Task 8: Update existing tests for new dependencies

**Files:**
- Modify: `tests/test_agent.py` — mock `degradation_mgr` instead of `llm_client`
- Modify: `tests/test_llm.py` — adapt for per-call timeout parameter

- [ ] **Step 1: Update agent test fixtures to mock DegradationManager**

In `tests/test_agent.py`, update the agent fixture:

```python
@pytest.fixture
def mock_degradation_mgr():
    """模拟降级管理器"""
    from unittest.mock import MagicMock

    from smartcs.services.common.degradation import GenerateResult
    from smartcs.shared.models import DegradationLevel

    mgr = MagicMock()
    mgr.level = DegradationLevel.NORMAL
    mgr.generate_with_fallback = AsyncMock(return_value=GenerateResult(
        content="模拟回复",
        source="llm",
    ))
    return mgr
```

Update all test cases that construct `SmartCSAgent` to pass `degradation_mgr` instead of `llm_client`.

- [ ] **Step 2: Update LLM test for timeout parameter**

In `tests/test_llm.py`, ensure any test that calls `chat()` still works with the new `timeout` optional parameter (backward compatible — no change needed if not using it).

- [ ] **Step 3: Run full test suite**

```bash
cd /Users/qiangli/CodeBuddy/agent_project && poetry run pytest tests/ -v --ignore=tests/test_bot_api.py
```

Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_agent.py tests/test_llm.py
git commit -m "test: update agent and llm tests for degradation manager integration"
```

---

### Task 9: Final integration verification

**Files:**
- Read: `tests/test_bot_api.py` — confirm it will pass or needs update

- [ ] **Step 1: Run full test suite including bot API**

```bash
cd /Users/qiangli/CodeBuddy/agent_project && poetry run pytest tests/ -v
```

Expected: All tests pass (bot API test may skip if middleware unavailable).

- [ ] **Step 2: Run lint + type check**

```bash
cd /Users/qiangli/CodeBuddy/agent_project && poetry run ruff check src/ && poetry run mypy src/
```

Expected: No errors.

- [ ] **Step 3: Commit any remaining changes**

```bash
git add -A
git commit -m "chore: final integration verification for Sprint 4 degradation strategy"
```
