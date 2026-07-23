"""坐席辅助引擎 — PydanticAI 实现

替代 Temporal OrchestrationWorkflow，使用 asyncio.gather + PydanticAI Agent 实现：
- 三路评估 (D1/D2/D3) 并行
- 三路执行 (E1/E2/E3) 条件并行
- 展示决策（场景+时间+反馈驱动）
- 仲裁融合 + PII 脱敏
- 熔断器 + 降级保护

与旧版兼容的返回格式，使 assist/router.py 改动最小化。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from smartcs.services.assist.ai_executor import AIExecutor
from smartcs.services.assist.arbitrator import GlobalArbitrator
from smartcs.services.common.circuit_breaker import CircuitBreaker, CircuitState
from smartcs.services.common.decision import (
    PushTracker,
    Scene,
    ShowDecision,
    detect_scene,
    should_show,
)
from smartcs.services.common.degradation import DegradationManager
from smartcs.shared.config import get_settings

logger = logging.getLogger(__name__)

# ── Prometheus 指标 ──

try:
    from prometheus_client import Counter, Histogram

    _PROMETHEUS_OK = True
except ImportError:
    _PROMETHEUS_OK = False

if _PROMETHEUS_OK:
    ASSIST_ENGINE_DECISIONS = Counter(
        "smartcs_assist_engine_decisions_total",
        "坐席辅助引擎决策计数",
        ["scene", "decision"],
    )
    ASSIST_ENGINE_LATENCY = Histogram(
        "smartcs_assist_engine_latency_seconds",
        "坐席辅助引擎耗时",
        ["phase"],
    )
    ASSIST_ENGINE_DEGRADATION = Counter(
        "smartcs_assist_engine_degradation_total",
        "坐席辅助引擎降级次数",
        ["agent", "reason"],
    )
else:
    # 测试环境无 prometheus_client 时的 mock
    class _MockMetric:
        def labels(self, **kwargs: Any) -> _MockMetric:
            return self

        def inc(self) -> None:
            pass

        def observe(self, _v: float) -> None:
            pass

    ASSIST_ENGINE_DECISIONS = _MockMetric()  # type: ignore[assignment]
    ASSIST_ENGINE_LATENCY = _MockMetric()  # type: ignore[assignment]
    ASSIST_ENGINE_DEGRADATION = _MockMetric()  # type: ignore[assignment]


# ── 评估结果 ──


@dataclass
class EvaluatorResult:
    """评估器输出"""

    activated: bool = False
    reason: str = ""


@dataclass
class ExecutorResult:
    """执行器输出"""

    executor_id: str = ""
    ui_schema: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    success: bool = True
    degraded: bool = False
    degradation_type: str = ""
    risk_action: str = "PASS"  # PASS / WARN / BLOCK
    trace_id: str = ""


@dataclass
class OrchestrationResult:
    """编排结果（与旧版 OrchestrationResult 兼容）"""

    primary_card: dict[str, Any] = field(default_factory=dict)
    risk_badge: dict[str, Any] | None = None
    marketing_slot: dict[str, Any] | None = None
    fusion_type: str = "service_only"
    trace_id: str = ""
    elapsed_ms: int = 0


# ── 评估函数（纯规则，不调 LLM）──


def evaluate_d1_service(state_snapshot: dict[str, Any]) -> EvaluatorResult:
    """D1 服务评估器: 意图置信度 > 阈值"""
    settings = get_settings().orchestration
    confidence = state_snapshot.get("last_confidence", 0.0)
    cooldown = state_snapshot.get("d1_cooldown_remaining", 0)

    if cooldown > 0:
        return EvaluatorResult(activated=False, reason=f"冷却中(剩余{cooldown}轮)")

    activated = confidence > settings.d1_intent_confidence_threshold
    reason = f"置信度={confidence:.2f} 阈值={settings.d1_intent_confidence_threshold}"
    return EvaluatorResult(activated=activated, reason=reason)


def evaluate_d2_marketing(state_snapshot: dict[str, Any]) -> EvaluatorResult:
    """D2 营销评估器: 情绪+意图+压制综合判断"""
    settings = get_settings().orchestration
    state = state_snapshot

    if state.get("suppress_flag", False):
        return EvaluatorResult(activated=False, reason="营销被压制(suppress_flag=true)")

    cooldown = state.get("d2_cooldown_remaining", 0)
    if cooldown > 0:
        return EvaluatorResult(activated=False, reason=f"冷却中(剩余{cooldown}轮)")

    emotion = state.get("emotion_vector")
    confidence = state.get("last_confidence", 0.0)

    if emotion and emotion.get("label") in ("positive", "neutral"):
        score = emotion.get("score", 0.0)
        if score > settings.d2_emotion_score_threshold and confidence > 0.5:
            return EvaluatorResult(
                activated=True,
                reason=f"情绪={emotion.get('label')}({score:.2f}) 置信度={confidence:.2f}",
            )
        return EvaluatorResult(
            activated=False,
            reason=f"条件不足: 情绪score={score:.2f} 置信度={confidence:.2f}",
        )

    return EvaluatorResult(activated=False, reason=f"情绪不满足: {emotion}")


def evaluate_d3_risk(_state_snapshot: dict[str, Any]) -> EvaluatorResult:
    """D3 风控评估器: 始终激活"""
    return EvaluatorResult(activated=True, reason="风控始终激活")


# ── 编排管道 ──


async def run_assist_engine(
    session_id: str,
    message: str,
    intent: str,
    confidence: float,
    trace_id: str,
    state_snapshot: dict[str, Any],
    ai_executor: AIExecutor,
    alert_engine: Any = None,
    degrader: DegradationManager | None = None,
    breakers: dict[str, CircuitBreaker] | None = None,
    push_tracker: PushTracker | None = None,
    redis_client: Any = None,
    session_manager: Any = None,
    sentiment: str = "neutral",
    sentiment_score: float = 0.5,
) -> dict[str, Any] | None:
    """执行 坐席辅助引擎

    替代 Temporal OrchestrationWorkflow.run()，使用 asyncio.gather 并行。

    Args:
        session_id: 会话 ID
        message: 客户消息
        intent: 意图标签字符串
        confidence: 意图置信度
        trace_id: 追踪 ID（用于幂等去重）
        state_snapshot: 会话状态快照
        ai_executor: AI 执行器实例
        alert_engine: 合规告警引擎（用于风控执行器）
        degrader: 降级管理器
        breakers: 熔断器字典 {"ai": breaker, "mkt": breaker, "risk": breaker}
        push_tracker: 推送追踪器（per-session）
        redis_client: Redis 客户端（用于幂等去重和状态写回）

    Returns:
        Push payload dict 或 None（如果不需要推送）
    """
    t0 = time.monotonic()
    settings = get_settings().orchestration
    breakers = breakers or {}
    push_tracker = push_tracker or PushTracker()

    # ── 幂等性检查 ──
    dedup_key = f"smartcs:ae:dedup:{trace_id}"
    if redis_client:
        try:
            cached = await redis_client.get(dedup_key)
            if cached:
                import json

                logger.debug("坐席辅助引擎幂等命中: trace_id=%s", trace_id)
                return json.loads(cached)
        except Exception:
            pass

    # ── Phase 1: 场景识别 + 评估（并行）──
    scene = detect_scene(message)

    d1 = evaluate_d1_service(state_snapshot)
    d2 = evaluate_d2_marketing(state_snapshot)
    d3 = evaluate_d3_risk(state_snapshot)

    logger.debug(
        "坐席辅助引擎评估 session=%s scene=%s d1=%s d2=%s d3=%s",
        session_id,
        scene.value,
        d1.activated,
        d2.activated,
        d3.activated,
    )

    # ── Phase 2: 并行执行（带熔断+降级+超时）──

    async def run_e1() -> ExecutorResult | None:
        """E1 AI 服务执行器"""
        if not d1.activated:
            return None

        ai_breaker = breakers.get("ai")
        if ai_breaker and ai_breaker.state == CircuitState.OPEN:
            ASSIST_ENGINE_DEGRADATION.labels(agent="ai", reason="breaker_open").inc()
            return ExecutorResult(
                executor_id="ai_service",
                degraded=True,
                degradation_type="breaker_open",
                trace_id=trace_id,
            )

        try:
            result = await asyncio.wait_for(
                ai_executor.run(
                    session_id=session_id,
                    message=message,
                    intent=intent,
                    state_snapshot=state_snapshot,
                    trace_id=trace_id,
                ),
                timeout=settings.e1_sla_ms / 1000,
            )
            if ai_breaker:
                ai_breaker.record_success(elapsed=result.get("latency_ms", 0) / 1000)

            return ExecutorResult(
                executor_id="ai_service",
                ui_schema=result.get("ui_schema", {}),
                latency_ms=result.get("latency_ms", 0),
                success=True,
                degraded=result.get("degraded", False),
                degradation_type=result.get("degradation_type", ""),
                trace_id=trace_id,
            )
        except TimeoutError:
            if ai_breaker:
                ai_breaker.record_slow_call(settings.e1_sla_ms / 1000)
            ASSIST_ENGINE_DEGRADATION.labels(agent="ai", reason="timeout").inc()
            logger.warning("E1 超时 session=%s", session_id)
            return ExecutorResult(
                executor_id="ai_service",
                degraded=True,
                degradation_type="timeout",
                trace_id=trace_id,
            )
        except Exception as e:
            if ai_breaker:
                ai_breaker.record_failure()
            ASSIST_ENGINE_DEGRADATION.labels(agent="ai", reason=str(e)[:50]).inc()
            logger.warning("E1 异常 session=%s: %s", session_id, e)
            return ExecutorResult(
                executor_id="ai_service",
                degraded=True,
                degradation_type="safe_fallback",
                trace_id=trace_id,
            )

    async def run_e3() -> ExecutorResult:
        """E3 风控执行器"""
        risk_breaker = breakers.get("risk")
        if risk_breaker and risk_breaker.state == CircuitState.OPEN:
            ASSIST_ENGINE_DEGRADATION.labels(agent="risk", reason="breaker_open").inc()
            return ExecutorResult(
                executor_id="risk",
                degraded=True,
                degradation_type="pass_with_audit_flag",
                risk_action="PASS",
                trace_id=trace_id,
            )

        if alert_engine is None:
            return ExecutorResult(
                executor_id="risk",
                risk_action="PASS",
                ui_schema={"action": "PASS"},
                trace_id=trace_id,
            )

        try:
            t_risk = time.monotonic()
            # E3 SLA: 与 E1 并行时取 min(E1.SLA, E3.SLA)
            e3_timeout = settings.e3_sla_ms / 1000
            if d1.activated:
                e3_timeout = min(settings.e1_sla_ms, settings.e3_sla_ms) / 1000

            loop = asyncio.get_running_loop()
            alerts = await asyncio.wait_for(
                loop.run_in_executor(None, alert_engine.check_compliance, message),
                timeout=e3_timeout,
            )
            elapsed = int((time.monotonic() - t_risk) * 1000)

            if risk_breaker:
                risk_breaker.record_success(elapsed=elapsed / 1000)

            has_critical = any(a.get("level") == "critical" for a in alerts)
            has_warning = any(a.get("level") == "warning" for a in alerts)

            if has_critical:
                return ExecutorResult(
                    executor_id="risk",
                    risk_action="BLOCK",
                    latency_ms=elapsed,
                    ui_schema={
                        "action": "BLOCK",
                        "reason": "合规风险",
                        "alerts": alerts,
                    },
                    trace_id=trace_id,
                )
            elif has_warning:
                return ExecutorResult(
                    executor_id="risk",
                    risk_action="WARN",
                    latency_ms=elapsed,
                    ui_schema={
                        "action": "WARN",
                        "alerts": alerts,
                    },
                    trace_id=trace_id,
                )
            else:
                return ExecutorResult(
                    executor_id="risk",
                    risk_action="PASS",
                    latency_ms=elapsed,
                    ui_schema={"action": "PASS"},
                    trace_id=trace_id,
                )
        except TimeoutError:
            if risk_breaker:
                risk_breaker.record_failure()
            ASSIST_ENGINE_DEGRADATION.labels(agent="risk", reason="timeout").inc()
            logger.warning("E3 超时 session=%s", session_id)
            return ExecutorResult(
                executor_id="risk",
                degraded=True,
                degradation_type="pass_with_audit_flag",
                risk_action="PASS",
                ui_schema={"action": "PASS", "risk_pending_audit": True},
                trace_id=trace_id,
            )
        except Exception as e:
            if risk_breaker:
                risk_breaker.record_failure()
            ASSIST_ENGINE_DEGRADATION.labels(agent="risk", reason=str(e)[:50]).inc()
            return ExecutorResult(
                executor_id="risk",
                degraded=True,
                degradation_type="pass_with_audit_flag",
                risk_action="PASS",
                ui_schema={"action": "PASS", "risk_pending_audit": True},
                trace_id=trace_id,
            )

    # E1 + E3 并行执行
    e1_result, e3_result = await asyncio.gather(
        run_e1(),
        run_e3(),
    )

    # E2 延迟执行（营销在服务后，场景条件检查）
    e2_result: ExecutorResult | None = None
    e3_blocked = e3_result.risk_action == "BLOCK" if e3_result else False

    d2_should_run = (
        d2.activated
        and not e3_blocked  # 风控拦截跳过营销
        and scene != Scene.URGENT  # 紧急场景跳过营销
    )
    # 策略 1: D1 激活时压制 D2 持续 N 轮（文档 §3.3 service_suppresses_marketing）
    if d2_should_run and d1.activated:
        remaining = state_snapshot.get("d2_suppress_rounds", 0)
        if remaining > 0:
            d2_should_run = False
            logger.debug("OE: D2 被 D1 压制，剩余 %d 轮", remaining)

    # 策略: D1+D2 都激活时，营销延迟 500ms 追加
    if d2_should_run:
        mkt_breaker = breakers.get("mkt")
        if mkt_breaker is None or mkt_breaker.state != CircuitState.OPEN:
            if d1.activated:
                await asyncio.sleep(settings.marketing_defer_ms / 1000)

            # E2: 营销推荐（规则引擎）
            from smartcs.services.assist.marketing_executor import evaluate_marketing
            from smartcs.shared.models import IntentLabel, SentimentLabel

            try:
                intent_label = IntentLabel(intent)
            except ValueError:
                intent_label = IntentLabel.FAQ
            # 传入真实情绪标签，使"负面情绪不营销"等规则生效（此前硬编码 NEUTRAL 导致规则不触发）
            try:
                sentiment_label = SentimentLabel(sentiment)
            except ValueError:
                sentiment_label = SentimentLabel.NEUTRAL
            cards = evaluate_marketing(intent=intent_label, sentiment=sentiment_label)
            e2_result = ExecutorResult(
                executor_id="marketing",
                ui_schema={
                    "marketing_cards": [
                        {
                            "product_id": c.product_id,
                            "product_name": c.product_name,
                            "product_type": c.product_type,
                            "reason": c.reason,
                            "priority": c.priority,
                            "risk_tip": c.risk_tip,
                        }
                        for c in cards
                    ]
                }
                if cards
                else {"marketing_cards": []},
                latency_ms=0,
                trace_id=trace_id,
            )

    # ── Phase 3: 展示决策 ──
    risk_action = e3_result.risk_action if e3_result else "PASS"

    ai_decision = (
        should_show("ai", scene, push_tracker)
        if e1_result and not e1_result.degraded
        else ShowDecision(should_show=False, reason="")
    )
    mkt_decision = (
        should_show("marketing", scene, push_tracker) if e2_result else ShowDecision(should_show=False, reason="")
    )
    risk_decision = should_show("risk", scene, push_tracker, risk_action=risk_action)

    logger.debug(
        "坐席辅助引擎展示决策 session=%s ai=%s(%s) mkt=%s(%s) risk=%s(%s)",
        session_id,
        "show" if ai_decision.should_show else "skip",
        ai_decision.reason,
        "show" if mkt_decision.should_show else "skip",
        mkt_decision.reason,
        "show" if risk_decision.should_show else "skip",
        risk_decision.reason,
    )

    # ── Phase 4: 仲裁融合 ──
    arbitrator = GlobalArbitrator()

    # 构造 results dict 给仲裁器
    from smartcs.services.assist.arbitrator import ExecutorOutput as ArbExecutorOutput

    ai_output = (
        ArbExecutorOutput(
            executor_id="ai_service",
            ui_schema=e1_result.ui_schema if e1_result else {},
            latency_ms=e1_result.latency_ms if e1_result else 0,
            success=e1_result.success if e1_result else False,
            degraded=e1_result.degraded if e1_result else True,
            degradation_type=e1_result.degradation_type if e1_result else "",
            trace_id=trace_id,
        )
        if ai_decision.should_show
        else None
    )

    risk_output = (
        ArbExecutorOutput(
            executor_id="risk",
            ui_schema=e3_result.ui_schema if e3_result else {},
            latency_ms=e3_result.latency_ms if e3_result else 0,
            risk_action=risk_action,
            trace_id=trace_id,
        )
        if risk_decision.should_show
        else None
    )

    mkt_output = (
        ArbExecutorOutput(
            executor_id="marketing",
            ui_schema=e2_result.ui_schema if e2_result else {},
            latency_ms=e2_result.latency_ms if e2_result else 0,
            trace_id=trace_id,
        )
        if mkt_decision.should_show
        else None
    )

    results: dict[str, ArbExecutorOutput] = {}
    if ai_output:
        results["ai_service"] = ai_output
    if risk_output:
        results["risk"] = risk_output
    if mkt_output:
        results["marketing"] = mkt_output

    arbitration = await arbitrator.arbitrate(results, state_snapshot)

    # ── Phase 5: 状态写回 + 幂等缓存 ──
    push_tracker.record_push("ai")
    if mkt_decision.should_show:
        push_tracker.record_push("marketing")
    if risk_decision.should_show:
        push_tracker.record_push("risk")

    # ── Phase 5b: 编排状态回写到 session meta key ──
    # 将 intent_stack/entity_pool/emotion_vector/suppress_flag/risk_pending_audit
    # 通过 CAS patch 写回，使后续 坐席辅助引擎周期能读取累积状态
    if session_manager is not None:
        state_patches: dict[str, Any] = {}

        # 意图栈增量（去重追加）
        current_stack = state_snapshot.get("intent_stack", [])
        if intent and intent not in current_stack:
            state_patches["intent_stack"] = current_stack + [intent]

        # 风控待审标记
        if risk_action in ("BLOCK", "WARN"):
            state_patches["risk_pending_audit"] = True

        # 紧急场景压制营销（单向门 false→true）
        if scene == Scene.URGENT and not state_snapshot.get("suppress_flag", False):
            state_patches["suppress_flag"] = True

        # 策略 1: D1 激活 → 重置 D2 压制轮次计数器
        if d1.activated:
            state_patches["d2_suppress_rounds"] = settings.d1_cooldown_turns
        else:
            # 非 D1 激活轮次：递减剩余压制轮次
            remaining = state_snapshot.get("d2_suppress_rounds", 0)
            if remaining > 0:
                state_patches["d2_suppress_rounds"] = remaining - 1

        # 情绪向量更新（时间窗口替换）
        if sentiment and sentiment != "neutral":
            state_patches["emotion_vector"] = {
                "label": sentiment,
                "score": sentiment_score,
                "updated_at": datetime.now(UTC).isoformat(),
            }

        if state_patches:
            expected_version = state_snapshot.get("version", 1)
            try:
                await session_manager.patch_state(
                    session_id=session_id,
                    expected_version=expected_version,
                    patches=state_patches,
                    writer=f"assist_engine:{trace_id}",
                )
                logger.debug(
                    "坐席辅助引擎状态回写成功: session=%s patches=%s",
                    session_id,
                    list(state_patches.keys()),
                )
            except Exception as e:
                logger.warning("坐席辅助引擎状态回写失败: session=%s error=%s", session_id, e)

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    push_data: dict[str, Any] = {
        "type": "assist_push",
        "session_id": session_id,
        "trigger": "customer_message",
        "payload": {
            "primary_card": arbitration.primary_card,
            "risk_badge": arbitration.risk_badge,
            "marketing_slot": arbitration.marketing_slot,
            "fusion_type": arbitration.fusion_type,
            "trace_id": trace_id,
        },
        "feedback_token": f"{session_id}:{trace_id}:{int(t0 * 1000)}",
    }

    # 缓存幂等结果
    if redis_client:
        try:
            import json

            await redis_client.setex(dedup_key, 30, json.dumps(push_data, default=str))
            # 保存 push_tracker 状态
            await redis_client.setex(
                f"smartcs:ae:tracker:{session_id}",
                3600,  # 1 小时 TTL
                json.dumps(push_tracker.to_dict()),
            )
        except Exception:
            pass

    # ── 可观测性 ──
    ASSIST_ENGINE_DECISIONS.labels(
        scene=scene.value,
        decision=(
            f"ai={'show' if ai_decision.should_show else 'skip'},"
            f"mkt={'show' if mkt_decision.should_show else 'skip'},"
            f"risk={risk_action}"
        ),
    ).inc()
    ASSIST_ENGINE_LATENCY.labels(phase="total").observe(elapsed_ms / 1000)

    logger.info(
        "坐席辅助引擎编排完成 session=%s scene=%s ai=%s mkt=%s risk=%s " "ai_lat=%dms risk_lat=%dms total=%dms",
        session_id,
        scene.value,
        "show" if ai_decision.should_show else "skip",
        "show" if mkt_decision.should_show else "skip",
        risk_action,
        e1_result.latency_ms if e1_result else 0,
        e3_result.latency_ms if e3_result else 0,
        elapsed_ms,
    )

    return push_data


# ── PushTracker 加载/保存辅助 ──


async def load_push_tracker(session_id: str, redis_client: Any) -> PushTracker:
    """从 Redis 加载推送追踪器"""
    if redis_client is None:
        return PushTracker()

    try:
        import json

        data = await redis_client.get(f"smartcs:ae:tracker:{session_id}")
        if data:
            return PushTracker.from_dict(json.loads(data))
    except Exception:
        pass

    return PushTracker()


async def save_push_tracker(session_id: str, tracker: PushTracker, redis_client: Any) -> None:
    """保存推送追踪器到 Redis"""
    if redis_client is None:
        return

    try:
        import json

        await redis_client.setex(
            f"oe:tracker:{session_id}",
            3600,
            json.dumps(tracker.to_dict()),
        )
    except Exception:
        pass
