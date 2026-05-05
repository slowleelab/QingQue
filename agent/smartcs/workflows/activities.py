"""Temporal Activities

三路评估器 (D1/D2/D3) + 三路执行器 (E1/E2/E3)
对应设计文档 §3.3 评估器 + §3.4 执行器。
"""
from __future__ import annotations

import logging

from temporalio import activity

from smartcs.workflows.shared import EvaluatorInput, EvaluatorOutput, ExecutorInput, ExecutorOutput

logger = logging.getLogger(__name__)


@activity.defn
async def evaluate_d1_service(input: EvaluatorInput) -> EvaluatorOutput:
    """服务评估器 (D1): 意图置信度 > 阈值，2 轮冷却期"""
    return EvaluatorOutput(activated=False, reason="placeholder")


@activity.defn
async def evaluate_d2_marketing(input: EvaluatorInput) -> EvaluatorOutput:
    """营销评估器 (D2): 情绪 + 意图 + Suppress"""
    return EvaluatorOutput(activated=False, reason="placeholder")


@activity.defn
async def evaluate_d3_risk(input: EvaluatorInput) -> EvaluatorOutput:
    """风控评估器 (D3): 始终激活"""
    return EvaluatorOutput(activated=True, reason="风控始终激活")


@activity.defn
async def execute_e1_ai_service(input: ExecutorInput) -> ExecutorOutput:
    """AI 服务执行器 (E1) — placeholder"""
    return ExecutorOutput(executor_id="ai_service")


@activity.defn
async def execute_e2_marketing(input: ExecutorInput) -> ExecutorOutput:
    """营销执行器 (E2) — placeholder"""
    return ExecutorOutput(executor_id="marketing")


@activity.defn
async def execute_e3_risk(input: ExecutorInput) -> ExecutorOutput:
    """风控执行器 (E3) — placeholder"""
    return ExecutorOutput(executor_id="risk", risk_action="PASS")
