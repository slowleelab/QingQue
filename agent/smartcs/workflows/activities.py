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


# ── 状态管理 Activities（供 Workflow 内部调用）──


@activity.defn
async def read_state_snapshot(session_id: str) -> dict[str, Any] | None:
    """读取最新状态快照（M4: 在 Workflow 内部读取，而非路由层冻结）"""
    try:
        from smartcs.services.common.state_manager import StateManager
        from smartcs.shared.config import get_settings

        settings = get_settings()
        redis = get_redis_from_app()
        if redis is None:
            return None
        mgr = StateManager(redis=redis, ttl=settings.session.ttl_seconds)
        return await mgr.get_snapshot(session_id)
    except Exception as e:
        logger.warning("读取状态快照失败: session=%s error=%s", session_id, e)
        return None


_redis_instance: Any = None


def set_redis_for_activities(redis: Any) -> None:
    """设置 Activities 使用的 Redis 实例（依赖注入）"""
    global _redis_instance
    _redis_instance = redis


def get_redis_from_app() -> Any:
    """获取 Redis 实例"""
    return _redis_instance


@activity.defn
async def cas_write_state(session_id: str, patches: dict[str, Any]) -> dict[str, Any]:
    """S2: CAS 写回状态变更

    供 Workflow 在执行器完成后写回状态变更。
    """
    try:
        from smartcs.services.common.state_manager import StateManager
        from smartcs.shared.config import get_settings

        redis = get_redis_from_app()
        if redis is None:
            return {"ok": False, "reason": "redis_unavailable"}

        settings = get_settings()
        mgr = StateManager(redis=redis, ttl=settings.session.ttl_seconds)

        # 读取当前版本
        snapshot = await mgr.get_snapshot(session_id)
        if snapshot is None:
            # 初始化状态
            snapshot = await mgr.init_state(session_id, {})

        expected_version = snapshot.get("version", 1)
        return await mgr.cas_write(
            session_id=session_id,
            expected_version=expected_version,
            patches=patches,
            writer="orchestration_workflow",
            max_retries=1,
        )
    except Exception as e:
        logger.warning("CAS 写入失败: session=%s error=%s", session_id, e)
        return {"ok": False, "reason": str(e)}


# ── 评估器 Activities ──


@activity.defn
async def evaluate_d1_service(eval_input: EvaluatorInput) -> EvaluatorOutput:
    """服务评估器 (D1): 意图置信度 > 阈值，2 轮冷却期

    对应文档 §3.3: D1 服务评估器
    S3: 冷却值由 Workflow CAS 写回管理，评估器仅读取和判断。
    """
    settings = get_settings().orchestration
    state = eval_input.state_snapshot

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
    # S3: 激活后返回 cooldown 值，供 Workflow CAS 写回
    cooldown_remaining = settings.d1_cooldown_turns if activated else 0
    return EvaluatorOutput(activated=activated, reason=reason, cooldown_remaining=cooldown_remaining)


@activity.defn
async def evaluate_d2_marketing(eval_input: EvaluatorInput) -> EvaluatorOutput:
    """营销评估器 (D2): 情绪 + 意图 + Suppress 综合判断，5 轮冷却期

    对应文档 §3.3: D2 营销评估器（含动态阈值熔断，避免过度营销）
    S3: 冷却值由 Workflow CAS 写回管理，评估器仅读取和判断。
    """
    settings = get_settings().orchestration
    state = eval_input.state_snapshot

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
        # M7: 使用 EmotionVector.decayed_score() 计算衰减后的分数
        try:
            from datetime import UTC, datetime

            from smartcs.shared.models import EmotionVector

            ev = EmotionVector(**emotion) if isinstance(emotion, dict) else emotion
            updated_at = ev.updated_at
            delta = (datetime.now(UTC) - updated_at).total_seconds()
            if delta > 0:
                score = ev.decayed_score(delta)
        except Exception:
            pass  # 降级为原始分数
        if score > settings.d2_emotion_score_threshold and confidence > 0.5:
            activated = True
            reason = f"情绪={emotion.get('label')}({score:.2f}) 置信度={confidence:.2f}"
        else:
            reason = f"条件不足: 情绪score={score:.2f} 置信度={confidence:.2f}"
    else:
        reason = f"情绪不满足: {emotion}"

    # S3: 激活后返回 cooldown 值，供 Workflow CAS 写回
    cooldown_remaining = settings.d2_cooldown_turns if activated else 0
    return EvaluatorOutput(activated=activated, reason=reason, cooldown_remaining=cooldown_remaining)


@activity.defn
async def evaluate_d3_risk(eval_input: EvaluatorInput) -> EvaluatorOutput:
    """风控评估器 (D3): 始终激活

    对应文档 §3.3: D3 风控评估器（始终激活，无冷却期）
    """
    return EvaluatorOutput(activated=True, reason="风控始终激活", cooldown_remaining=0)


# ── H5: 幂等性去重存储 ──

_dedup_store: dict[str, ExecutorOutput] = {}


def _dedup_key(trace_id: str, executor_id: str) -> str:
    """生成幂等性去重 key"""
    return f"{trace_id}:{executor_id}"


def _check_dedup(trace_id: str, executor_id: str) -> ExecutorOutput | None:
    """检查是否已执行（幂等性）"""
    return _dedup_store.get(_dedup_key(trace_id, executor_id))


def _record_dedup(trace_id: str, executor_id: str, result: ExecutorOutput) -> None:
    """记录执行结果（幂等性）"""
    _dedup_store[_dedup_key(trace_id, executor_id)] = result


def reset_dedup_store() -> None:
    """重置幂等性存储（用于测试）"""
    global _dedup_store
    _dedup_store = {}


# ── 执行器 Activities ──


@activity.defn
async def execute_e1_ai_service(exec_input: ExecutorInput) -> ExecutorOutput:
    """AI 服务执行器 (E1): LangGraph DAG，SLA 3s

    对应文档 §3.4: E1 AI 服务执行器
    降级策略: 降级为快速通路结果；无快速通路结果则返回安全兜底
    H5: 幂等性 — trace_id + executor_id 去重
    """
    # H5: 幂等性检查
    dedup_result = _check_dedup(exec_input.trace_id, "ai_service")
    if dedup_result is not None:
        logger.debug("E1 幂等命中: trace_id=%s", exec_input.trace_id)
        return dedup_result

    breaker = _get_ai_breaker()
    settings = get_settings().orchestration

    # 熔断器打开 → 直接降级
    if breaker.state == CircuitState.OPEN:
        logger.warning("E1 熔断器打开，降级到快速通路")
        result = ExecutorOutput(
            executor_id="ai_service",
            degraded=True,
            degradation_type="fast_path_fallback",
            trace_id=exec_input.trace_id,
        )
        _record_dedup(exec_input.trace_id, "ai_service", result)
        return result

    t0 = time.monotonic()
    try:
        dag = _get_ai_dag()
        result = await asyncio.wait_for(
            dag.run(
                session_id=exec_input.session_id,
                message=exec_input.message,
                intent=exec_input.intent,
                sentiment=exec_input.sentiment,
                confidence=exec_input.state_snapshot.get("last_confidence", 0.0),
                state_snapshot=exec_input.state_snapshot,
                trace_id=exec_input.trace_id,
            ),
            timeout=settings.e1_sla_ms / 1000,
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        breaker.record_success(elapsed=elapsed / 1000)

        output = ExecutorOutput(
            executor_id="ai_service",
            ui_schema=result.get("ui_schema", {}),
            latency_ms=elapsed,
            success=True,
            degraded=result.get("degraded", False),
            degradation_type=result.get("degradation_type", ""),
            trace_id=exec_input.trace_id,
        )
        _record_dedup(exec_input.trace_id, "ai_service", output)
        return output
    except TimeoutError:
        elapsed = int((time.monotonic() - t0) * 1000)
        breaker.record_slow_call(elapsed / 1000)
        # 修复: 如果 DAG 有快速通路结果，降级到 fast_path_fallback；否则降级到 safe_fallback
        degradation_type = "safe_fallback"
        ui_schema: dict[str, Any] = {}
        last = getattr(dag, "_last_result", None)
        if isinstance(last, dict) and last.get("fast_path_hit"):
            degradation_type = "fast_path_fallback"
            ui_schema = last.get("ui_schema", {})
        logger.warning("E1 超时 (%dms)，降级到 %s", elapsed, degradation_type)
        output = ExecutorOutput(
            executor_id="ai_service",
            degraded=True,
            degradation_type=degradation_type,
            ui_schema=ui_schema,
            latency_ms=elapsed,
            trace_id=exec_input.trace_id,
        )
        _record_dedup(exec_input.trace_id, "ai_service", output)
        return output
    except Exception as e:
        breaker.record_failure()
        logger.warning("E1 异常: %s，降级到安全兜底", e)
        output = ExecutorOutput(
            executor_id="ai_service",
            degraded=True,
            degradation_type="safe_fallback",
            latency_ms=int((time.monotonic() - t0) * 1000),
            trace_id=exec_input.trace_id,
        )
        _record_dedup(exec_input.trace_id, "ai_service", output)
        return output


@activity.defn
async def execute_e2_marketing(exec_input: ExecutorInput) -> ExecutorOutput:
    """营销执行器 (E2): 纯 Activity 接口

    对应文档 §3.4: E2 营销执行器 (gRPC 营销微服务)
    当前无真实 gRPC 营销微服务，返回空营销卡片。
    后续对接真实 gRPC 服务只需替换本 Activity 内部实现。
    降级策略: 不展示营销卡片
    H5: 幂等性 — trace_id + executor_id 去重
    """
    # H5: 幂等性检查
    dedup_result = _check_dedup(exec_input.trace_id, "marketing")
    if dedup_result is not None:
        return dedup_result

    breaker = _get_mkt_breaker()

    if breaker.state == CircuitState.OPEN:
        result = ExecutorOutput(
            executor_id="marketing",
            degraded=True,
            degradation_type="skip_card",
            trace_id=exec_input.trace_id,
        )
        _record_dedup(exec_input.trace_id, "marketing", result)
        return result

    # TODO: 对接 gRPC 营销微服务
    # 当前返回空营销卡片
    breaker.record_success()
    result = ExecutorOutput(
        executor_id="marketing",
        ui_schema={"marketing_cards": []},
        latency_ms=0,
        trace_id=exec_input.trace_id,
    )
    _record_dedup(exec_input.trace_id, "marketing", result)
    return result


@activity.defn
async def execute_e3_risk(exec_input: ExecutorInput) -> ExecutorOutput:
    """风控执行器 (E3): 当前使用本地 AlertEngine

    对应文档 §3.4: E3 风控执行器 (Java RPC 规则引擎)
    当前使用本地 AlertEngine 作为实现。
    后续对接 Java RPC 规则引擎只需替换本 Activity 内部实现。
    降级策略: 放行 + 标记 risk_pending_audit: true
    H5: 幂等性 — trace_id + executor_id 去重
    """
    # H5: 幂等性检查
    dedup_result = _check_dedup(exec_input.trace_id, "risk")
    if dedup_result is not None:
        return dedup_result

    breaker = _get_risk_breaker()
    settings = get_settings().orchestration

    if breaker.state == CircuitState.OPEN:
        result = ExecutorOutput(
            executor_id="risk",
            degraded=True,
            degradation_type="pass_with_audit_flag",
            risk_action="PASS",
            ui_schema={"action": "PASS", "risk_pending_audit": True},
            trace_id=exec_input.trace_id,
        )
        _record_dedup(exec_input.trace_id, "risk", result)
        return result

    t0 = time.monotonic()
    try:
        alert_engine = _get_risk_alert_engine()
        # AlertEngine.check_compliance is synchronous, run in executor
        loop = asyncio.get_running_loop()
        alerts = await asyncio.wait_for(
            loop.run_in_executor(None, alert_engine.check_compliance, exec_input.message),
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

        output = ExecutorOutput(
            executor_id="risk",
            ui_schema=ui,
            latency_ms=elapsed,
            risk_action=action,
            trace_id=exec_input.trace_id,
        )
        _record_dedup(exec_input.trace_id, "risk", output)
        return output
    except TimeoutError:
        elapsed = int((time.monotonic() - t0) * 1000)
        breaker.record_failure()
        logger.warning("E3 超时 (%dms)，降级为放行+待审", elapsed)
        result = ExecutorOutput(
            executor_id="risk",
            degraded=True,
            degradation_type="pass_with_audit_flag",
            risk_action="PASS",
            ui_schema={"action": "PASS", "risk_pending_audit": True},
            latency_ms=elapsed,
            trace_id=exec_input.trace_id,
        )
        _record_dedup(exec_input.trace_id, "risk", result)
        return result
    except Exception as e:
        breaker.record_failure()
        logger.warning("E3 异常: %s，降级为放行+待审", e)
        result = ExecutorOutput(
            executor_id="risk",
            degraded=True,
            degradation_type="pass_with_audit_flag",
            risk_action="PASS",
            ui_schema={"action": "PASS", "risk_pending_audit": True},
            latency_ms=int((time.monotonic() - t0) * 1000),
            trace_id=exec_input.trace_id,
        )
        _record_dedup(exec_input.trace_id, "risk", result)
        return result
