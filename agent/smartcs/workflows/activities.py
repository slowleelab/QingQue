"""Temporal Activities

三路评估器 (D1/D2/D3) + 三路执行器 (E1/E2/E3)
对应设计文档 §3.3 评估器 + §3.4 执行器。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from temporalio import activity

from smartcs.services.common.circuit_breaker import CircuitBreaker, CircuitState
from smartcs.shared.config import get_settings
from smartcs.workflows.shared import EvaluatorInput, EvaluatorOutput, ExecutorInput, ExecutorOutput

logger = logging.getLogger(__name__)

# ── 熔断器实例（模块级单例） ──

_ai_breaker: CircuitBreaker | None = None
_mkt_breaker: CircuitBreaker | None = None
_risk_breaker: CircuitBreaker | None = None

# E1 DAG 实例（延迟初始化）
_ai_dag: Any = None


def _get_ai_breaker() -> CircuitBreaker:
    global _ai_breaker
    if _ai_breaker is None:
        cfg = get_settings().circuit_breaker
        _ai_breaker = CircuitBreaker(
            name="ai_executor",
            failure_threshold=cfg.ai_failure_rate_threshold,
            slow_call_rate_threshold=cfg.ai_slow_call_rate_threshold,
            slow_call_duration=cfg.ai_slow_call_duration_ms / 1000,
            recovery_timeout=cfg.ai_wait_duration_open_s,
            half_open_max_calls=3,
            sliding_window_size=cfg.ai_sliding_window_size,
        )
    return _ai_breaker


def _get_mkt_breaker() -> CircuitBreaker:
    global _mkt_breaker
    if _mkt_breaker is None:
        cfg = get_settings().circuit_breaker
        _mkt_breaker = CircuitBreaker(
            name="marketing_executor",
            failure_threshold=cfg.mkt_failure_rate_threshold,
            slow_call_rate_threshold=cfg.mkt_slow_call_rate_threshold,
            slow_call_duration=cfg.mkt_slow_call_duration_ms / 1000,
            recovery_timeout=cfg.mkt_wait_duration_open_s,
            sliding_window_size=cfg.mkt_sliding_window_size,
        )
    return _mkt_breaker


def _get_risk_breaker() -> CircuitBreaker:
    global _risk_breaker
    if _risk_breaker is None:
        cfg = get_settings().circuit_breaker
        _risk_breaker = CircuitBreaker(
            name="risk_executor",
            failure_threshold=cfg.risk_failure_rate_threshold,
            slow_call_rate_threshold=cfg.risk_slow_call_rate_threshold,
            slow_call_duration=cfg.risk_slow_call_duration_ms / 1000,
            recovery_timeout=cfg.risk_wait_duration_open_s,
            sliding_window_size=cfg.risk_sliding_window_size,
        )
    return _risk_breaker


def _get_ai_dag() -> Any:
    """获取或创建 AIExecutorDAG 实例"""
    global _ai_dag
    if _ai_dag is None:
        from smartcs.services.assist.ai_executor_dag import AIExecutorDAG
        from smartcs.services.assist.alert_engine import AlertEngine
        from smartcs.services.assist.script_service import ScriptService

        script_service = ScriptService()
        script_service.load_from_memory()
        alert_engine = AlertEngine()
        alert_engine.load_from_memory()

        _ai_dag = AIExecutorDAG(
            script_service=script_service,
            alert_engine=alert_engine,
        )
    return _ai_dag


def set_ai_dag(dag: Any) -> None:
    """设置 AIExecutorDAG 实例（用于依赖注入和测试）"""
    global _ai_dag
    _ai_dag = dag


# 风控执行器的 AlertEngine
_risk_alert_engine: Any = None


def set_alert_engine_for_risk(engine: Any) -> None:
    """设置风控执行器使用的 AlertEngine（用于依赖注入和测试）"""
    global _risk_alert_engine
    _risk_alert_engine = engine


def _get_risk_alert_engine() -> Any:
    global _risk_alert_engine
    if _risk_alert_engine is None:
        from smartcs.services.assist.alert_engine import AlertEngine

        engine = AlertEngine()
        engine.load_from_memory()
        _risk_alert_engine = engine
    return _risk_alert_engine


def reset_breakers() -> None:
    """重置所有熔断器单例（用于测试）"""
    global _ai_breaker, _mkt_breaker, _risk_breaker, _ai_dag, _risk_alert_engine
    _ai_breaker = None
    _mkt_breaker = None
    _risk_breaker = None
    _ai_dag = None
    _risk_alert_engine = None


# ── 评估器 Activities ──


@activity.defn
async def evaluate_d1_service(input: EvaluatorInput) -> EvaluatorOutput:
    """服务评估器 (D1): 意图置信度 > 阈值，2 轮冷却期

    对应文档 §3.3: D1 服务评估器
    """
    settings = get_settings().orchestration
    state = input.state_snapshot

    confidence = state.get("last_confidence", 0.0)
    cooldown = state.get("d1_cooldown_remaining", 0)

    if cooldown > 0:
        return EvaluatorOutput(
            activated=False,
            reason=f"冷却中(剩余{cooldown}轮)",
            cooldown_remaining=cooldown,
        )

    activated = confidence > settings.d1_intent_confidence_threshold
    reason = f"置信度={confidence:.2f} 阈值={settings.d1_intent_confidence_threshold}"
    return EvaluatorOutput(activated=activated, reason=reason, cooldown_remaining=0)


@activity.defn
async def evaluate_d2_marketing(input: EvaluatorInput) -> EvaluatorOutput:
    """营销评估器 (D2): 情绪 + 意图 + Suppress 综合判断，5 轮冷却期

    对应文档 §3.3: D2 营销评估器（含动态阈值熔断，避免过度营销）
    """
    settings = get_settings().orchestration
    state = input.state_snapshot

    # Suppress 检查
    if state.get("suppress_flag", False):
        return EvaluatorOutput(activated=False, reason="营销被压制(suppress_flag=true)")

    # 冷却期检查
    cooldown = state.get("d2_cooldown_remaining", 0)
    if cooldown > 0:
        return EvaluatorOutput(
            activated=False,
            reason=f"冷却中(剩余{cooldown}轮)",
            cooldown_remaining=cooldown,
        )

    # 情绪 + 意图 综合判断
    emotion = state.get("emotion_vector")
    confidence = state.get("last_confidence", 0.0)

    activated = False
    reason = ""

    if emotion and emotion.get("label") in ("positive", "neutral"):
        score = emotion.get("score", 0.0)
        if score > settings.d2_emotion_score_threshold and confidence > 0.5:
            activated = True
            reason = f"情绪={emotion['label']}({score:.2f}) 置信度={confidence:.2f}"
        else:
            reason = f"条件不足: 情绪score={score:.2f} 置信度={confidence:.2f}"
    else:
        reason = f"情绪不满足: {emotion}"

    return EvaluatorOutput(activated=activated, reason=reason, cooldown_remaining=0)


@activity.defn
async def evaluate_d3_risk(input: EvaluatorInput) -> EvaluatorOutput:
    """风控评估器 (D3): 始终激活

    对应文档 §3.3: D3 风控评估器（始终激活，无冷却期）
    """
    return EvaluatorOutput(activated=True, reason="风控始终激活", cooldown_remaining=0)


# ── 执行器 Activities ──


@activity.defn
async def execute_e1_ai_service(input: ExecutorInput) -> ExecutorOutput:
    """AI 服务执行器 (E1): LangGraph DAG，SLA 3s

    对应文档 §3.4: E1 AI 服务执行器
    降级策略: 降级为快速通路结果；无快速通路结果则返回安全兜底
    """
    breaker = _get_ai_breaker()
    settings = get_settings().orchestration

    # 熔断器打开 → 直接降级
    if breaker.state == CircuitState.OPEN:
        logger.warning("E1 熔断器打开，降级到快速通路")
        return ExecutorOutput(
            executor_id="ai_service",
            degraded=True,
            degradation_type="fast_path_fallback",
            trace_id=input.trace_id,
        )

    t0 = time.monotonic()
    try:
        dag = _get_ai_dag()
        result = await asyncio.wait_for(
            dag.run(
                session_id=input.session_id,
                message=input.message,
                intent=input.intent,
                sentiment=input.sentiment,
                confidence=input.state_snapshot.get("last_confidence", 0.0),
                state_snapshot=input.state_snapshot,
                trace_id=input.trace_id,
            ),
            timeout=settings.e1_sla_ms / 1000,
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        breaker.record_success(elapsed=elapsed / 1000)

        return ExecutorOutput(
            executor_id="ai_service",
            ui_schema=result.get("ui_schema", {}),
            latency_ms=elapsed,
            success=True,
            degraded=result.get("degraded", False),
            degradation_type=result.get("degradation_type", ""),
            trace_id=input.trace_id,
        )
    except asyncio.TimeoutError:
        elapsed = int((time.monotonic() - t0) * 1000)
        breaker.record_slow_call(elapsed / 1000)
        logger.warning("E1 超时 (%dms)，降级", elapsed)
        return ExecutorOutput(
            executor_id="ai_service",
            degraded=True,
            degradation_type="fast_path_fallback",
            latency_ms=elapsed,
            trace_id=input.trace_id,
        )
    except Exception as e:
        breaker.record_failure()
        logger.warning("E1 异常: %s，降级到安全兜底", e)
        return ExecutorOutput(
            executor_id="ai_service",
            degraded=True,
            degradation_type="safe_fallback",
            latency_ms=int((time.monotonic() - t0) * 1000),
            trace_id=input.trace_id,
        )


@activity.defn
async def execute_e2_marketing(input: ExecutorInput) -> ExecutorOutput:
    """营销执行器 (E2): 纯 Activity 接口

    对应文档 §3.4: E2 营销执行器 (gRPC 营销微服务)
    当前无真实 gRPC 营销微服务，返回空营销卡片。
    后续对接真实 gRPC 服务只需替换本 Activity 内部实现。
    降级策略: 不展示营销卡片
    """
    breaker = _get_mkt_breaker()

    if breaker.state == CircuitState.OPEN:
        return ExecutorOutput(
            executor_id="marketing",
            degraded=True,
            degradation_type="skip_card",
            trace_id=input.trace_id,
        )

    # TODO: 对接 gRPC 营销微服务
    # 当前返回空营销卡片
    breaker.record_success()
    return ExecutorOutput(
        executor_id="marketing",
        ui_schema={"marketing_cards": []},
        latency_ms=0,
        trace_id=input.trace_id,
    )


@activity.defn
async def execute_e3_risk(input: ExecutorInput) -> ExecutorOutput:
    """风控执行器 (E3): 当前使用本地 AlertEngine

    对应文档 §3.4: E3 风控执行器 (Java RPC 规则引擎)
    当前使用本地 AlertEngine 作为实现。
    后续对接 Java RPC 规则引擎只需替换本 Activity 内部实现。
    降级策略: 放行 + 标记 risk_pending_audit: true
    """
    breaker = _get_risk_breaker()
    settings = get_settings().orchestration

    if breaker.state == CircuitState.OPEN:
        return ExecutorOutput(
            executor_id="risk",
            degraded=True,
            degradation_type="pass_with_audit_flag",
            risk_action="PASS",
            ui_schema={"action": "PASS", "risk_pending_audit": True},
            trace_id=input.trace_id,
        )

    t0 = time.monotonic()
    try:
        alert_engine = _get_risk_alert_engine()
        # AlertEngine.check_compliance is synchronous, run in executor
        loop = asyncio.get_running_loop()
        alerts = await asyncio.wait_for(
            loop.run_in_executor(None, alert_engine.check_compliance, input.message),
            timeout=settings.e3_sla_ms / 1000,
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        breaker.record_success(elapsed=elapsed / 1000)

        has_critical = any(a.get("level") == "critical" for a in alerts)
        has_warning = any(a.get("level") == "warning" for a in alerts)

        if has_critical:
            action = "BLOCK"
            ui = {
                "action": "BLOCK",
                "reason": "合规风险",
                "alerts": [{"level": a.get("level"), "message": a.get("message")} for a in alerts],
            }
        elif has_warning:
            action = "WARN"
            ui = {
                "action": "WARN",
                "alerts": [{"level": a.get("level"), "message": a.get("message")} for a in alerts],
            }
        else:
            action = "PASS"
            ui = {"action": "PASS"}

        return ExecutorOutput(
            executor_id="risk",
            ui_schema=ui,
            latency_ms=elapsed,
            risk_action=action,
            trace_id=input.trace_id,
        )
    except asyncio.TimeoutError:
        elapsed = int((time.monotonic() - t0) * 1000)
        breaker.record_failure()
        logger.warning("E3 超时 (%dms)，降级为放行+待审", elapsed)
        return ExecutorOutput(
            executor_id="risk",
            degraded=True,
            degradation_type="pass_with_audit_flag",
            risk_action="PASS",
            ui_schema={"action": "PASS", "risk_pending_audit": True},
            latency_ms=elapsed,
            trace_id=input.trace_id,
        )
    except Exception as e:
        breaker.record_failure()
        logger.warning("E3 异常: %s，降级为放行+待审", e)
        return ExecutorOutput(
            executor_id="risk",
            degraded=True,
            degradation_type="pass_with_audit_flag",
            risk_action="PASS",
            ui_schema={"action": "PASS", "risk_pending_audit": True},
            latency_ms=int((time.monotonic() - t0) * 1000),
            trace_id=input.trace_id,
        )
