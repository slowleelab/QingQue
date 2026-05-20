"""编排引擎 Temporal Workflow

对应设计文档 §3.3 宏观编排层。
OE 状态机: IDLE → EVALUATING → DISPATCHING → WAITING_RESULTS → COMPLETED
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from smartcs.workflows.shared import (
    EvaluatorInput,
    EvaluatorOutput,
    ExecutorInput,
    ExecutorOutput,
    OrchestrationResult,
)

logger = logging.getLogger(__name__)

# Activity 不重试（执行器自行处理重试/降级）
_NO_RETRY = RetryPolicy(maximum_attempts=1)


def get_orchestration_settings():
    """获取编排层配置（延迟导入避免循环依赖）"""
    from smartcs.shared.config import get_settings
    return get_settings().orchestration


@workflow.defn
class OrchestrationWorkflow:
    """首席会话编排器 Workflow

    每次客户消息触发一次完整的 OE 周期。
    严格按照设计文档 §3.3 实现。
    """

    def __init__(self) -> None:
        # M1: OE 状态跟踪
        self._oe_state: str = "IDLE"
        self._activation_history: list[dict] = []
        # H1: suppress 持续轮数计数
        self._suppress_remaining: int = 0

    @workflow.run
    async def run(self, exec_input: ExecutorInput) -> OrchestrationResult:
        """执行完整的 OE 周期"""
        t_start = time.monotonic()
        settings = get_orchestration_settings()
        global_timeout_ms = settings.global_timeout_ms
        deadline = t_start + global_timeout_ms / 1000

        # M4: 在 Workflow 内部读取最新状态快照（而非使用路由层冻结的快照）
        state_snapshot = await workflow.execute_activity(
            "read_state_snapshot",
            exec_input.session_id,
            start_to_close_timeout=timedelta(seconds=1),
            retry_policy=_NO_RETRY,
        )
        if state_snapshot is None:
            state_snapshot = exec_input.state_snapshot
        # 用最新快照更新 exec_input
        exec_input = ExecutorInput(
            session_id=exec_input.session_id,
            message=exec_input.message,
            intent=exec_input.intent,
            sentiment=exec_input.sentiment,
            sentiment_history=exec_input.sentiment_history,
            state_snapshot=state_snapshot,
            trace_id=exec_input.trace_id,
        )

        # ── IDLE → EVALUATING ──
        self._oe_state = "EVALUATING"

        # H1: 递减 suppress 剩余轮数
        if self._suppress_remaining > 0:
            self._suppress_remaining -= 1
            if self._suppress_remaining == 0:
                # suppress 已过期，需要 CAS 写回清除
                pass

        # 并行执行 D1/D2/D3 评估
        d1_result, d2_result, d3_result = await asyncio.gather(
            workflow.execute_activity(
                "evaluate_d1_service",
                EvaluatorInput(session_id=exec_input.session_id, state_snapshot=exec_input.state_snapshot),
                start_to_close_timeout=timedelta(seconds=2),
                retry_policy=_NO_RETRY,
            ),
            workflow.execute_activity(
                "evaluate_d2_marketing",
                EvaluatorInput(session_id=exec_input.session_id, state_snapshot=exec_input.state_snapshot),
                start_to_close_timeout=timedelta(seconds=2),
                retry_policy=_NO_RETRY,
            ),
            workflow.execute_activity(
                "evaluate_d3_risk",
                EvaluatorInput(session_id=exec_input.session_id, state_snapshot=exec_input.state_snapshot),
                start_to_close_timeout=timedelta(seconds=2),
                retry_policy=_NO_RETRY,
            ),
        )

        # M1: 记录激活历史
        self._activation_history.append({
            "d1": d1_result.activated,
            "d2": d2_result.activated,
            "d3": d3_result.activated,
        })

        # M6: 可观测性 — 决策日志
        logger.info(
            "OE决策 trace=%s session=%s d1=%s d2=%s d3=%s suppress_rem=%d",
            exec_input.trace_id, exec_input.session_id,
            d1_result.activated, d2_result.activated, d3_result.activated,
            self._suppress_remaining,
        )

        # ── EVALUATING → DISPATCHING ──
        self._oe_state = "DISPATCHING"
        # 应用编排策略矩阵
        plan = self._apply_policies(d1_result, d2_result, d3_result)

        # ── DISPATCHING → WAITING_RESULTS ──
        self._oe_state = "WAITING_RESULTS"
        results: dict[str, ExecutorOutput] = {}

        # H3: 全局超时检查 — 如果已接近截止时间，跳过执行器直接返回部分结果
        now = time.monotonic()
        if now >= deadline:
            logger.warning("OE全局超时: session=%s — 跳过执行器", exec_input.session_id)
            return OrchestrationResult(
                primary_card={"type": "timeout_partial", "content": {}},
                fusion_type="timeout_partial",
                trace_id=exec_input.trace_id,
                elapsed_ms=int((time.monotonic() - t_start) * 1000),
            )

        # 策略: service_with_risk_parallel — E1 和 E3 并行执行
        # H4: E3 并行执行时 timeout = min(E1.SLA, E3.SLA) = 100ms
        parallel_tasks = []
        if d1_result.activated:
            parallel_tasks.append(("ai_service", "execute_e1_ai_service", timedelta(milliseconds=settings.e1_sla_ms)))
        if d3_result.activated:
            e3_timeout_ms = settings.e3_sla_ms
            # H4: 如果 E1 和 E3 并行，E3 的 timeout 取 min(E1.SLA, E3.SLA)
            if d1_result.activated:
                e3_timeout_ms = min(settings.e1_sla_ms, settings.e3_sla_ms)
            parallel_tasks.append(("risk", "execute_e3_risk", timedelta(milliseconds=e3_timeout_ms)))

        if parallel_tasks:
            exec_results = await asyncio.gather(
                *[
                    workflow.execute_activity(
                        activity_name,
                        exec_input,
                        start_to_close_timeout=timeout,
                        retry_policy=_NO_RETRY,
                    )
                    for _, activity_name, timeout in parallel_tasks
                ]
            )
            for (name, _, _), result in zip(parallel_tasks, exec_results, strict=False):
                results[name] = result

        # 策略: risk_block_skip_marketing — 风控拦截后跳过营销
        risk_output = results.get("risk")
        skip_marketing = risk_output is not None and risk_output.risk_action == "BLOCK"

        # 策略: marketing_deferred — 营销在服务后追加 (delay 500ms)
        # H3: 全局超时检查 — 如果剩余时间不足，跳过营销
        d2_should_run = d2_result.activated and not plan.get("d2_suppressed") and not skip_marketing
        remaining_time = deadline - time.monotonic()
        if d2_should_run and remaining_time > settings.marketing_defer_ms / 1000 + settings.e2_sla_ms / 1000:
            if d1_result.activated:
                # M5: 使用 workflow.sleep 代替 asyncio.sleep
                await workflow.sleep(timedelta(milliseconds=settings.marketing_defer_ms))
            mkt_result = await workflow.execute_activity(
                "execute_e2_marketing",
                exec_input,
                start_to_close_timeout=timedelta(milliseconds=settings.e2_sla_ms),
                retry_policy=_NO_RETRY,
            )
            results["marketing"] = mkt_result

        # ── WAITING_RESULTS → COMPLETED ──
        self._oe_state = "COMPLETED"

        # M6: 可观测性 — 执行结果日志
        result_summary = {name: f"{r.executor_id}({r.risk_action or 'ok'})" for name, r in results.items()}
        logger.info(
            "OE执行 trace=%s session=%s results=%s",
            exec_input.trace_id, exec_input.session_id, result_summary,
        )

        # S1: 使用 GlobalArbitrator（含 PII 脱敏 + 合规过滤）
        arbitration = await self._arbitrate_with_masking(results, exec_input)

        # S2: CAS 写回状态变更
        await self._cas_write_back(exec_input, d1_result, d2_result, d3_result, results)

        # 计算耗时
        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        arbitration.elapsed_ms = elapsed_ms

        # H3: 全局超时检查 — 如果已超时，标记结果为部分
        if elapsed_ms > global_timeout_ms:
            logger.warning(
                "Workflow 超时 session=%s elapsed=%dms > %dms",
                exec_input.session_id, elapsed_ms, global_timeout_ms,
            )

        return arbitration

    def _apply_policies(
        self,
        d1: EvaluatorOutput,
        d2: EvaluatorOutput,
        d3: EvaluatorOutput,
    ) -> dict:
        """应用编排策略矩阵

        对应文档 §3.3 编排策略矩阵:
        1. service_suppresses_marketing: D1.activated → D2.force_suppress=true, duration=2轮
        2. service_with_risk_parallel: D1 AND D3 → [E1, E3].parallel_execute()
        3. risk_block_skip_marketing: E3.result.action=="BLOCK" → D2.force_skip=true
        4. marketing_deferred: D1 AND D2 → E2.execute_after(E1.complete, delay=500ms)
        """
        # 策略 1: 互斥规则 — 服务主动时压制营销，duration=2轮（H1）
        d2_suppressed = d1.activated and d2.activated
        if d2_suppressed:
            self._suppress_remaining = 2  # H1: suppress 持续 2 轮

        return {"d2_suppressed": d2_suppressed}

    async def _arbitrate_with_masking(
        self,
        results: dict[str, ExecutorOutput],
        exec_input: ExecutorInput,
    ) -> OrchestrationResult:
        """仲裁融合 + PII 脱敏 + 合规过滤

        对应文档 §3.5 仲裁与输出层 + §4.2 安全与隐私。
        S1: 通过 GlobalArbitrator 确保所有输出经过 PII 脱敏。
        M2: 统一使用 GlobalArbitrator，移除重复仲裁逻辑。
        """
        from smartcs.services.assist.arbitrator import GlobalArbitrator

        arbitrator = GlobalArbitrator()
        result = await arbitrator.arbitrate(results, exec_input.state_snapshot)
        return result

    async def _cas_write_back(
        self,
        exec_input: ExecutorInput,
        d1: EvaluatorOutput,
        d2: EvaluatorOutput,
        d3: EvaluatorOutput,
        results: dict[str, ExecutorOutput],
    ) -> None:
        """S2: CAS 写回状态变更

        执行器完成后，将状态变更写回 Redis:
        - intent_stack: 增量合并
        - emotion_vector: 时间窗口替换
        - suppress_flag: 单向门（含 duration 追踪, H1）
        - risk_pending_audit: 全量覆写
        - node_position: 全量覆写
        - d1_cooldown_remaining / d2_cooldown_remaining: 评估器冷却值（S3）
        """
        # 收集需要写回的 patches
        patches: dict[str, Any] = {}

        # S3: 评估器冷却值写回
        if d1.activated:
            patches["d1_cooldown_remaining"] = get_orchestration_settings().d1_cooldown_turns
        elif d1.cooldown_remaining > 0:
            patches["d1_cooldown_remaining"] = d1.cooldown_remaining - 1

        if d2.activated:
            patches["d2_cooldown_remaining"] = get_orchestration_settings().d2_cooldown_turns
        elif d2.cooldown_remaining > 0:
            patches["d2_cooldown_remaining"] = d2.cooldown_remaining - 1

        # H1: suppress_flag 写回
        if self._suppress_remaining > 0:
            patches["suppress_flag"] = True
        else:
            # suppress duration 到期，使用 force_clear 标记绕过单向门
            patches["suppress_flag"] = False
            patches["suppress_force_clear"] = True

        # 风控待审标记
        risk_result = results.get("risk")
        if risk_result and risk_result.degraded and risk_result.degradation_type == "pass_with_audit_flag":
            patches["risk_pending_audit"] = True

        # 意图栈更新
        if exec_input.intent:
            patches["intent_stack"] = [exec_input.intent]

        if not patches:
            return

        # 执行 CAS 写回（通过 Activity）
        try:
            await workflow.execute_activity(
                "cas_write_state",
                args=[exec_input.session_id, patches],
                start_to_close_timeout=timedelta(seconds=2),
                retry_policy=_NO_RETRY,
            )
        except Exception as e:
            logger.warning("CAS 写回失败: session=%s error=%s", exec_input.session_id, e)
