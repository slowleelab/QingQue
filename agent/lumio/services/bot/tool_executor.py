"""工具调用编排器

拥有「LLM ↔ MCP 工具」多轮循环，以及敏感操作的确认状态机逻辑。
本模块是 P0 工具层在 Python 编排侧的核心：

- 非敏感工具：直接执行 → 出参脱敏 → 审计 → 回喂 LLM → 继续循环
- 敏感工具（挂失/调额/账单分期等）：不立即执行，暂存 ``PendingAction``，
  返回确认话术，短路循环；下一轮由 ``bot_agent`` 拦截确认后再执行

红线：任何 LLM/MCP 异常向上抛出，由调用方（bot_agent）回落到既有降级链。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from lumio.services.bot.tool_guard import GuardDecision
from lumio.services.common.audit import write_audit_log
from lumio.services.common.llm import ToolCall
from lumio.shared.metrics import TOOL_CALLS, TOOL_CONFIRMATIONS, TOOL_GUARD_DENIALS
from lumio.shared.models import PendingAction
from lumio.shared.pii import mask_pii

if TYPE_CHECKING:
    from collections.abc import Collection

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from lumio.services.bot.tool_guard import ToolGuard
    from lumio.services.common.llm import LLMClient
    from lumio.services.common.mcp_client import MCPToolClient
    from lumio.shared.config import MCPSettings

logger = logging.getLogger(__name__)

# 护栏拒绝时对用户的统一话术（不外泄内部原因）
_GUARD_REFUSAL = "很抱歉，该操作目前无法为您办理。如需帮助，我可以为您转接人工客服。"

ConfirmDecision = Literal["confirm", "cancel", "unclear"]

# 确认/取消关键词（cancel 优先判定，规避「不确认」这类否定表述）
_CANCEL_KEYWORDS = (
    "取消",
    "不用",
    "不要",
    "不办",
    "不确认",
    "不同意",
    "不可以",
    "算了",
    "放弃",
    "别",
    "停",
    "no",
    "cancel",
)
_CONFIRM_KEYWORDS = (
    "确认",
    "确定",
    "是的",
    "好的",
    "可以",
    "继续",
    "同意",
    "办理",
    "ok",
    "yes",
)


def detect_confirmation(text: str) -> ConfirmDecision:
    """纯关键词判定用户对待确认操作的意图

    P2 第三轮修复: 要求"整句"为纯确认/取消 — 旧实现子串匹配,
    "好的，另外帮我查下账单" 命中 "好的" → 误触发挂失/调额等敏感工具, 新问题被吞.
    判定规则: 去噪(标点/空白/语气词)后, 整句与关键词集合匹配才判定; 含附加内容 → unclear.

    优先判定取消（否定优先），再判定确认，否则 unclear。
    """
    if not text:
        return "unclear"
    normalized = text.strip().lower()
    # 去噪: 仅去除标点/空白/语气词 — 注意不能移除关键词内含的字 (如"好的"的"的",
    # "不用了"的"了"), 否则关键词被破坏无法匹配
    import re as _re

    core = _re.sub(r"[\s，。！？,.!?、～~嗯哦啊呀呢吧]", "", normalized)

    # 取消优先 (否定表述优先判定, 规避"不确认"被误判为确认)
    for kw in _CANCEL_KEYWORDS:
        # 整句核心内容就是取消词(或取消词 + 语气词), 无附加内容
        if core == kw or (core.startswith(kw) and len(core) <= len(kw) + 2):
            return "cancel"

    for kw in _CONFIRM_KEYWORDS:
        if core == kw or (core.startswith(kw) and len(core) <= len(kw) + 2):
            return "confirm"
    return "unclear"


@dataclass
class ToolExecutionResult:
    """工具循环产出

    - ``pending_action`` 非空 → 命中敏感工具，需用户确认（``content`` 为确认话术）
    - ``pending_action`` 为空 → LLM 已给出最终答复（``content``）
    """

    content: str
    source: str  # "llm" / "tool"
    pending_action: PendingAction | None = None
    executed_tools: list[str] = field(default_factory=list)
    # P2-19: 护栏拒绝/配额超限时置 True — 触发真实转人工 (此前只文案引导)
    should_transfer: bool = False
    transfer_reason: str = ""


class ToolCallingExecutor:
    """LLM 工具调用循环 + 敏感操作确认状态机"""

    def __init__(
        self,
        mcp_client: MCPToolClient,
        llm_client: LLMClient,
        audit_session_factory: async_sessionmaker[AsyncSession] | None,
        settings: MCPSettings,
        guard: ToolGuard | None = None,
    ) -> None:
        self._mcp = mcp_client
        self._llm = llm_client
        self._audit_factory = audit_session_factory
        self._settings = settings
        self._guard = guard

    # ── 对外入口 ──

    def has_tools(self) -> bool:
        """是否有可用工具（MCP 已连接且工具目录非空）"""
        return bool(self._mcp.to_openai_tools())

    async def run_conversation(
        self,
        *,
        system_prompt: str,
        user_input: str,
        history: list[dict[str, str]],
        session_id: str,
        actor_id: str,
        actor_role: str = "customer",
        trace_id: str = "",
        tool_names: Collection[str] | None = None,
    ) -> ToolExecutionResult:
        """常规业务办理：LLM 自主决定是否调用工具，跑多轮循环

        遇敏感工具 → 短路，返回 ``pending_action``。

        Args:
            tool_names: 可选的工具名白名单（渐进式暴露）。为 ``None`` 时暴露全部工具
                （默认，零行为变化）；由掌握意图的上游（bot_agent）按需传入子集。
        """
        tools = self._mcp.to_openai_tools(tool_names)
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_input})
        return await self._run_loop(
            messages,
            tools,
            session_id=session_id,
            actor_id=actor_id,
            actor_role=actor_role,
            trace_id=trace_id,
        )

    async def execute_confirmed_action(
        self,
        *,
        pending: PendingAction,
        system_prompt: str,
        history: list[dict[str, str]],
        session_id: str,
        actor_id: str,
        actor_role: str = "customer",
    ) -> ToolExecutionResult:
        """用户确认后执行暂存工具，并继续循环生成最终答复"""
        # 确认执行阶段刻意暴露全部工具：待确认工具已定，续跑仅用于生成答复；
        # 若在此筛掉候选，反而可能丢失续跑所需工具，故不做渐进式暴露。
        tools = self._mcp.to_openai_tools()
        tool_call = ToolCall(
            id=pending.tool_call_id or "confirmed_call", name=pending.tool_name, arguments=pending.arguments
        )

        # 确认后再次校验护栏（防止确认期间参数被篡改 / 权限变化）
        guard_decision = await self._enforce_guard(
            tool_call, session_id=session_id, actor_id=actor_id, actor_role=actor_role
        )
        if not guard_decision.allowed:
            return ToolExecutionResult(content=_GUARD_REFUSAL, source="guard", executed_tools=[])

        # 先执行已确认的敏感工具（脱敏 + 审计）
        tool_message = await self._execute_and_audit(
            tool_call,
            session_id=session_id,
            actor_id=actor_id,
            actor_role=actor_role,
        )

        # 构造消息序列：system + history +（合成 assistant tool_call）+ tool 结果
        assistant_msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
                    },
                }
            ],
        }
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append(assistant_msg)
        messages.append(tool_message)

        result = await self._run_loop(
            messages,
            tools,
            session_id=session_id,
            actor_id=actor_id,
            actor_role=actor_role,
            initial_executed=[tool_call.name],
        )
        return result

    # ── 内部循环 ──

    async def _run_loop(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        session_id: str,
        actor_id: str,
        actor_role: str,
        trace_id: str = "",
        initial_executed: list[str] | None = None,
    ) -> ToolExecutionResult:
        executed: list[str] = list(initial_executed or [])

        for _ in range(self._settings.max_tool_iterations):
            result = await self._llm.chat_with_tools(messages, tools)

            if not result.has_tool_calls:
                return ToolExecutionResult(
                    content=result.content,
                    source="llm",
                    executed_tools=executed,
                )

            # 记录 assistant 的 tool_calls（回喂 API 需原样带上）
            messages.append(result.raw_message)

            for tool_call in result.tool_calls:
                # P1-4 第三轮修复: 执行侧白名单校验 — 渐进式暴露只过滤"给 LLM 看"的一侧,
                # 执行侧此前对幻觉工具名直接透传后端 (未知工具 is_sensitive 还返回 False → 免确认).
                # 现强制: 工具名必须存在于注册缓存, 否则拒绝执行并回喂错误.
                if self._mcp.get_tool(tool_call.name) is None:
                    logger.warning(
                        "拒绝未注册工具调用 (幻觉): name=%s session=%s",
                        tool_call.name,
                        session_id,
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": f"工具 {tool_call.name!r} 不存在, 请勿调用",
                        }
                    )
                    executed.append(tool_call.name)
                    continue

                # 护栏（授权 + 额度）→ 拒绝则短路，不执行、不进入确认
                guard_decision = await self._enforce_guard(
                    tool_call, session_id=session_id, actor_id=actor_id, actor_role=actor_role
                )
                if not guard_decision.allowed:
                    # P2-19: 护栏拒绝 → 真实转人工 (文案 _GUARD_REFUSAL 已引导, 但此前
                    # should_transfer 恒 False, 客户看到"可以转接"还得再发一条消息)
                    return ToolExecutionResult(
                        content=_GUARD_REFUSAL,
                        source="guard",
                        executed_tools=executed,
                        should_transfer=True,
                        transfer_reason=f"tool_guard_refused: {tool_call.name} ({guard_decision.reason})",
                    )

                # 敏感工具 → 短路，写待确认（不执行）
                if self._mcp.is_sensitive(tool_call.name):
                    pending = self._build_pending_action(tool_call, trace_id=trace_id)
                    TOOL_CONFIRMATIONS.labels(decision="pending").inc()
                    return ToolExecutionResult(
                        content=pending.confirm_prompt,
                        source="tool",
                        pending_action=pending,
                        executed_tools=executed,
                    )

                # 非敏感工具 → 执行 + 脱敏 + 审计 + 回喂
                tool_message = await self._execute_and_audit(
                    tool_call,
                    session_id=session_id,
                    actor_id=actor_id,
                    actor_role=actor_role,
                )
                messages.append(tool_message)
                executed.append(tool_call.name)

        # 循环上限保护
        raise RuntimeError(f"工具调用超过最大轮数 {self._settings.max_tool_iterations}")

    async def _execute_and_audit(
        self,
        tool_call: ToolCall,
        *,
        session_id: str,
        actor_id: str,
        actor_role: str,
    ) -> dict:
        """执行工具 → 出参脱敏 → 写审计 → 返回 tool message"""
        masked_args = mask_pii(json.dumps(tool_call.arguments, ensure_ascii=False))
        # P2-7 第五轮修复: 配额检查接线 — tool_robustness.ToolQuotaGuard 此前生产零调用,
        # TOOL_QUOTA_EXCEEDED 指标永不产生; 超配额直接拒绝, 不进 MCP
        try:
            from lumio.services.common.tool_robustness import get_quota_guard

            allowed, _count = await get_quota_guard().check_and_increment(actor_id, tool_call.name)
            if not allowed:
                TOOL_CALLS.labels(tool=tool_call.name, status="quota_exceeded").inc()
                return {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": "该操作今日调用次数已达上限, 请稍后再试或转人工办理。",
                }
        except Exception:
            pass  # 配额组件故障不阻断主流程
        try:
            # P2-7 第五轮修复: 网络类错误重试接线 (async_retry 此前生产零调用)
            from lumio.services.common.tool_robustness import async_retry

            @async_retry(max_attempts=3, tool_name=tool_call.name)
            async def _call_with_retry() -> dict:
                return await self._mcp.call_tool(tool_call.name, tool_call.arguments)

            raw = await _call_with_retry()
            is_error = bool(raw.get("is_error"))
            masked_content = mask_pii(str(raw.get("content", "")))
            # P1-2 上下文工程修复: 工具结果 4096 字节截断 — 旧代码原样回喂,
            # 5 轮工具循环累积可突破上下文预算 (prompt flooding); 截断提示 LLM 内容已截断
            max_size = getattr(self._settings, "tool_result_max_size_bytes", 4096) or 4096
            if len(masked_content.encode("utf-8")) > max_size:
                masked_content = masked_content[:max_size] + "\n...[工具结果已截断]"
            status = "error" if is_error else "success"
        except Exception as exc:
            TOOL_CALLS.labels(tool=tool_call.name, status="error").inc()
            await self._audit(
                actor_id=actor_id,
                actor_role=actor_role,
                action=f"tool.{tool_call.name}",
                target_id=session_id,
                detail={"arguments": masked_args, "error": str(exc)[:300]},
                status_code=500,
            )
            raise

        TOOL_CALLS.labels(tool=tool_call.name, status=status).inc()
        await self._audit(
            actor_id=actor_id,
            actor_role=actor_role,
            action=f"tool.{tool_call.name}",
            target_id=session_id,
            detail={"arguments": masked_args, "result": masked_content[:500], "is_error": is_error},
            status_code=500 if is_error else 200,
        )

        return {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": masked_content or "（工具无返回内容）",
        }

    def _build_pending_action(self, tool_call: ToolCall, *, trace_id: str) -> PendingAction:
        """构造待确认操作，生成确认话术"""
        spec = self._mcp.get_tool(tool_call.name)
        friendly = (spec.description if spec and spec.description else tool_call.name).strip()
        prompt = f"您确认要办理「{friendly}」吗？回复『确认』继续办理，回复『取消』放弃。"
        now = datetime.now(UTC)
        return PendingAction(
            tool_name=tool_call.name,
            arguments=tool_call.arguments,
            tool_call_id=tool_call.id,
            confirm_prompt=prompt,
            created_at=now,
            expires_at=now + timedelta(seconds=self._settings.confirmation_ttl_seconds),
            trace_id=trace_id,
        )

    async def _audit(
        self,
        *,
        actor_id: str,
        actor_role: str,
        action: str,
        target_id: str,
        detail: dict,
        status_code: int,
    ) -> None:
        if self._audit_factory is None:
            return
        await write_audit_log(
            self._audit_factory,
            actor_id=actor_id,
            actor_role=actor_role,
            action=action,
            target_type="tool",
            target_id=target_id,
            detail=detail,
            status_code=status_code,
        )

    async def _enforce_guard(
        self,
        tool_call: ToolCall,
        *,
        session_id: str,
        actor_id: str,
        actor_role: str,
    ) -> GuardDecision:
        """执行前护栏校验；拒绝时记录指标 + 审计（403），返回判定结果"""
        if self._guard is None or not self._guard.active:
            return GuardDecision(allowed=True)
        decision = self._guard.check(tool_call.name, tool_call.arguments, actor_role=actor_role)
        if decision.allowed:
            return decision
        TOOL_GUARD_DENIALS.labels(tool=tool_call.name, reason=decision.code or "denied").inc()
        masked_args = mask_pii(json.dumps(tool_call.arguments, ensure_ascii=False))
        await self._audit(
            actor_id=actor_id,
            actor_role=actor_role,
            action=f"tool.{tool_call.name}",
            target_id=session_id,
            detail={"arguments": masked_args, "denied": decision.reason, "guard": decision.code},
            status_code=403,
        )
        logger.info("工具护栏拦截: tool=%s reason=%s", tool_call.name, decision.code)
        return decision

    async def audit_decision(
        self,
        *,
        session_id: str,
        actor_id: str,
        actor_role: str,
        tool_name: str,
        decision: str,
    ) -> None:
        """审计敏感操作的确认决策（confirm/cancel/expired），补齐合规链路"""
        await self._audit(
            actor_id=actor_id,
            actor_role=actor_role,
            action=f"tool_confirm.{decision}",
            target_id=session_id,
            detail={"tool": tool_name, "decision": decision},
            status_code=200,
        )
