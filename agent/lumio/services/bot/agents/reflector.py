"""C2: Agent 自反思 / 自我纠错

ReAct 循环中的 Reflection step:
- tool 返回后, LLM 先评估 "结果合理吗? 是否需要重试/换工具?"
- 失败计数 > 3 → 切换 LLM (主模型 → 备用模型)
- 输出层加 Critic Agent: LLM 答复后用 guard LLM 检查合规

设计要点:
- 反思必须轻量 (不能拖慢主链路, 1 次反思调用 < 200ms)
- 反思失败 → 降级 (跳过反思, 信任主结果)
- 反思结果可观测 (metrics + audit)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from lumio.shared.logger import get_logger
from lumio.shared.metrics import AGENT_REFLECTION

logger = get_logger(__name__)


class ReflectionDecision(str, Enum):
    """反思决策."""

    PASS = "pass"  # 通过, 继续
    RETRY = "retry"  # 重试同工具
    SWITCH_TOOL = "switch_tool"  # 换其他工具
    ESCALATE = "escalate"  # 升级 (转人工 / 备用 LLM)
    FAIL = "fail"  # 整体失败


@dataclass
class ReflectionResult:
    """反思结果."""

    decision: ReflectionDecision
    reason: str
    confidence: float = 1.0
    suggested_action: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "confidence": self.confidence,
            "suggested_action": self.suggested_action,
        }


# ── 反思 prompt 模板 (轻量) ──

_REFLECTION_SYSTEM_PROMPT = """你是客服 AI 质量审查员. 评估 tool 调用结果是否合理.

## 评估维度

1. **结果完整性**: tool 返回是否含完整信息 (e.g. 调额结果必须含新额度/生效时间)
2. **结果合理性**: 数值是否在合理范围 (e.g. 调额不能超过 100 万, 不能为负)
3. **合规性**: 是否含敏感信息泄露, 是否违反业务规则

## 输出 JSON

```json
{
  "decision": "pass|retry|switch_tool|escalate|fail",
  "reason": "评估理由 (1-2 句)",
  "confidence": 0.9,
  "suggested_action": "如果决策是 retry/switch, 给出具体行动"
}
```"""


_REFLECTION_USER_TEMPLATE = """## 客户问题
{question}

## 调用的工具
{tool_name}

## 工具参数
{tool_args}

## 工具返回结果
{tool_result}

## 评估

请按 3 维度评估 tool 返回结果是否合理, 输出 JSON."""


class Reflector:
    """Agent 反思器 (单例, 轻量)."""

    def __init__(self, judge_model: str = "qwen2.5:7b", base_url: str | None = None) -> None:
        self.judge_model = judge_model
        self._base_url = base_url or "http://localhost:11434/v1"
        self._client: Any = None
        self._init_client()
        # 失败计数 (per session_id) — LRU 限制 10000 条, 防内存爆炸
        from collections import OrderedDict

        self._failure_count: OrderedDict[str, int] = OrderedDict()
        self._failure_count_max = 10000

    def _init_client(self) -> None:
        try:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                base_url=self._base_url, api_key="ollama", timeout=10.0  # 反思必须快
            )
        except ImportError:
            logger.warning("openai SDK 未安装, Reflector 不可用")

    async def reflect(
        self,
        question: str,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_result: Any,
        session_id: str | None = None,
    ) -> ReflectionResult:
        """对单次 tool 调用结果反思.

        失败兜底: 反射失败 → 默认 PASS (信任主结果, 避免阻塞).
        """
        if self._client is None:
            return ReflectionResult(
                decision=ReflectionDecision.PASS, reason="Reflector 不可用, 跳过反思"
            )

        import json

        user_prompt = _REFLECTION_USER_TEMPLATE.format(
            question=question,
            tool_name=tool_name,
            tool_args=json.dumps(tool_args, ensure_ascii=False),
            tool_result=json.dumps(tool_result, ensure_ascii=False)[:1000],  # 截断
        )

        try:
            api_result = await self._client.chat.completions.create(
                model=self.judge_model,
                messages=[
                    {"role": "system", "content": _REFLECTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=200,
                response_format={"type": "json_object"},
            )
            content = api_result.choices[0].message.content or "{}"
            data = json.loads(content)

            decision_str = data.get("decision", "pass")
            try:
                decision = ReflectionDecision(decision_str)
            except ValueError:
                decision = ReflectionDecision.PASS

            result = ReflectionResult(
                decision=decision,
                reason=data.get("reason", ""),
                confidence=float(data.get("confidence", 0.8)),
                suggested_action=data.get("suggested_action"),
            )

            AGENT_REFLECTION.labels(decision=decision.value).inc()

            # 失败计数 (LRU: 累加时移到末尾, 超出上限 LRU 淘汰)
            if session_id and decision in (ReflectionDecision.RETRY, ReflectionDecision.FAIL):
                self._failure_count[session_id] = self._failure_count.get(session_id, 0) + 1
                self._failure_count.move_to_end(session_id)
                if len(self._failure_count) > self._failure_count_max:
                    self._failure_count.popitem(last=False)  # 淘汰最旧
                # 连续失败 3 次 → 升级
                if self._failure_count[session_id] >= 3:
                    AGENT_REFLECTION.labels(decision="escalate").inc()
                    return ReflectionResult(
                        decision=ReflectionDecision.ESCALATE,
                        reason=f"连续 {self._failure_count[session_id]} 次失败, 升级处理",
                        suggested_action="transfer_to_agent",
                    )

            return result

        except Exception as exc:
            logger.warning("Reflector 调用失败, 降级 PASS: %s", exc)
            return ReflectionResult(
                decision=ReflectionDecision.PASS, reason=f"反思失败, 降级: {exc}"
            )

    def reset_failure_count(self, session_id: str) -> None:
        """会话成功完成后重置失败计数."""
        self._failure_count.pop(session_id, None)


# 全局单例
_reflector: Reflector | None = None


def get_reflector() -> Reflector:
    global _reflector
    if _reflector is None:
        _reflector = Reflector()
    return _reflector
