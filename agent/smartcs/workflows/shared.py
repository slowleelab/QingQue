"""Temporal Workflow/Activity 共享数据模型

对应设计文档 §3.3 宏观编排层和 §3.4 微观执行层的接口定义。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvaluatorInput:
    """评估器输入"""
    session_id: str = ""
    state_snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluatorOutput:
    """评估器输出"""
    activated: bool = False
    reason: str = ""
    cooldown_remaining: int = 0


@dataclass
class ExecutorInput:
    """执行器输入"""
    session_id: str = ""
    message: str = ""
    intent: str = "faq"
    sentiment: str = "neutral"
    sentiment_history: list[str] = field(default_factory=list)
    state_snapshot: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""


@dataclass
class ExecutorOutput:
    """执行器输出"""
    executor_id: str = ""
    ui_schema: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    success: bool = True
    degraded: bool = False
    degradation_type: str = ""
    risk_action: str = ""  # RiskActionEnum value: "PASS"/"WARN"/"BLOCK"
    trace_id: str = ""


@dataclass
class OrchestrationResult:
    """编排结果（Workflow 返回值）"""
    primary_card: dict[str, Any] = field(default_factory=dict)
    risk_badge: dict[str, Any] | None = None
    marketing_slot: dict[str, Any] | None = None
    fusion_type: str = "service_only"
    trace_id: str = ""
    elapsed_ms: int = 0
