"""编排引擎 Temporal Workflow

对应设计文档 §3.3 宏观编排层。
OE 状态机: IDLE → EVALUATING → DISPATCHING → WAITING_RESULTS → COMPLETED
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

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


@workflow.defn
class OrchestrationWorkflow:
    """首席会话编排器 Workflow

    每次客户消息触发一次完整的 OE 周期。
    严格按照设计文档 §3.3 实现。
    """

    @workflow.run
    async def run(self, input: ExecutorInput) -> OrchestrationResult:
        """执行完整的 OE 周期"""
        # ── IDLE → EVALUATING ──
        # 并行执行 D1/D2/D3 评估
        d1_result, d2_result, d3_result = await asyncio.gather(
            workflow.execute_activity(
                "evaluate_d1_service",
                EvaluatorInput(session_id=input.session_id, state_snapshot=input.state_snapshot),
                start_to_close_timeout=timedelta(seconds=2),
                retry_policy=_NO_RETRY,
            ),
            workflow.execute_activity(
                "evaluate_d2_marketing",
                EvaluatorInput(session_id=input.session_id, state_snapshot=input.state_snapshot),
                start_to_close_timeout=timedelta(seconds=2),
                retry_policy=_NO_RETRY,
            ),
            workflow.execute_activity(
                "evaluate_d3_risk",
                EvaluatorInput(session_id=input.session_id, state_snapshot=input.state_snapshot),
                start_to_close_timeout=timedelta(seconds=2),
                retry_policy=_NO_RETRY,
            ),
        )

        # ── EVALUATING → DISPATCHING ──
        # 应用编排策略矩阵
        plan = self._apply_policies(d1_result, d2_result, d3_result)

        # ── DISPATCHING → WAITING_RESULTS ──
        results: dict[str, ExecutorOutput] = {}

        # 策略: service_with_risk_parallel — E1 和 E3 并行执行
        parallel_tasks = []
        if d1_result.activated:
            parallel_tasks.append(("ai_service", "execute_e1_ai_service", timedelta(seconds=3)))
        if d3_result.activated:
            parallel_tasks.append(("risk", "execute_e3_risk", timedelta(seconds=3)))

        if parallel_tasks:
            exec_results = await asyncio.gather(
                *[
                    workflow.execute_activity(
                        activity_name,
                        input,
                        start_to_close_timeout=timeout,
                        retry_policy=_NO_RETRY,
                    )
                    for _, activity_name, timeout in parallel_tasks
                ]
            )
            for (name, _, _), result in zip(parallel_tasks, exec_results):
                results[name] = result

        # 策略: risk_block_skip_marketing — 风控拦截后跳过营销
        risk_output = results.get("risk")
        skip_marketing = risk_output is not None and risk_output.risk_action == "BLOCK"

        # 策略: marketing_deferred — 营销在服务后追加 (delay 500ms)
        d2_should_run = d2_result.activated and not plan.get("d2_suppressed") and not skip_marketing
        if d2_should_run:
            if d1_result.activated:
                # 延迟 500ms 后执行营销
                await asyncio.sleep(0.5)
            mkt_result = await workflow.execute_activity(
                "execute_e2_marketing",
                input,
                start_to_close_timeout=timedelta(milliseconds=500),
                retry_policy=_NO_RETRY,
            )
            results["marketing"] = mkt_result

        # ── WAITING_RESULTS → COMPLETED ──
        # 仲裁融合
        arbitration = self._arbitrate(results)

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
        # 策略 1: 互斥规则 — 服务主动时压制营销
        d2_suppressed = d1.activated and d2.activated

        return {"d2_suppressed": d2_suppressed}

    def _arbitrate(self, results: dict[str, ExecutorOutput]) -> OrchestrationResult:
        """优先级融合展示规则

        对应文档 §3.5:
        IF 风控.action == "BLOCK":
            主卡片 = 风控拦截提示（不可操作）
            辅助槽 = 服务回答（只读展示，灰色标记）
            营销槽 = 不展示
        ELIF 风控.action == "WARN":
            主卡片 = 服务回答
            风险标记 = 风控警告徽章
            营销槽 = 营销卡片（降级为小卡片）
        ELSE:  # 风控放行
            主卡片 = 服务回答
            辅助槽 = 营销卡片（标准展示）
        """
        risk = results.get("risk")
        ai = results.get("ai_service")
        mkt = results.get("marketing")

        risk_action = risk.risk_action if risk else "PASS"

        if risk_action == "BLOCK":
            return OrchestrationResult(
                primary_card={"type": "risk_block", "content": risk.ui_schema if risk else {}},
                risk_badge=None,
                marketing_slot=None,
                fusion_type="risk_blocked",
            )
        elif risk_action == "WARN":
            return OrchestrationResult(
                primary_card={"type": "service_answer", "content": ai.ui_schema if ai else {}},
                risk_badge={
                    "type": "risk_badge",
                    "alerts": risk.ui_schema.get("alerts", []) if risk else [],
                },
                marketing_slot=(
                    {"type": "marketing_small", "content": mkt.ui_schema if mkt else {}}
                    if mkt
                    else None
                ),
                fusion_type="service_risk_warn",
            )
        else:
            return OrchestrationResult(
                primary_card={"type": "service_answer", "content": ai.ui_schema if ai else {}},
                risk_badge=None,
                marketing_slot=(
                    {"type": "marketing_standard", "content": mkt.ui_schema if mkt else {}}
                    if mkt
                    else None
                ),
                fusion_type="service_marketing" if mkt else "service_only",
            )
