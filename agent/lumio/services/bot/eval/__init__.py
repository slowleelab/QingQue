"""Lumio Agent 评估模块.

提供:
- LLM-as-Judge (Qwen2.5-72B)
- Golden Set 回归测试
- CI Gate 集成
"""

from lumio.services.bot.eval.judge import (
    GoldenSet,
    JudgeDimension,
    JudgeResult,
    LLMJudge,
    run_regression,
)

__all__ = [
    "GoldenSet",
    "JudgeDimension",
    "JudgeResult",
    "LLMJudge",
    "run_regression",
]
