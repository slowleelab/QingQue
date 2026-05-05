"""编排引擎 Temporal Workflow

对应设计文档 §3.3 宏观编排层。
OE 状态机: IDLE → EVALUATING → DISPATCHING → WAITING_RESULTS → COMPLETED
"""
from __future__ import annotations

from temporalio import workflow

from smartcs.workflows.shared import ExecutorInput, OrchestrationResult


@workflow.defn
class OrchestrationWorkflow:
    """首席会话编排器 Workflow — placeholder"""

    @workflow.run
    async def run(self, input: ExecutorInput) -> OrchestrationResult:
        return OrchestrationResult(fusion_type="service_only", trace_id=input.trace_id)
