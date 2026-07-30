"""工具护栏（ToolGuard）

在 Python 编排侧对工具调用做**执行前**的纵深防御，与 Higress 网关治理互补：

1. **授权白名单**：按 ``actor_role`` 限制可调用的工具集合。
2. **额度校验**：对敏感/资金类工具的金额入参做上限校验（如分期金额、临时提额目标）。

设计红线：默认空配置 → 全部放行，行为与现状完全一致（零回归）。一旦配置非空，
即转为「显式允许」的保守策略——未在白名单内的角色/工具、超额的入参一律拒绝。
本模块为**纯同步、无 I/O**，便于单测与在热路径上零额外延迟。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from smartcs.shared.config import MCPSettings


@dataclass
class GuardDecision:
    """护栏判定结果

    - ``allowed=True`` → 放行
    - ``allowed=False`` → 拒绝；``code`` 用于指标标签，``reason`` 为可读原因（不外泄给用户原文）
    """

    allowed: bool
    code: str = ""
    reason: str = ""


class ToolGuard:
    """工具调用授权 + 额度校验"""

    def __init__(self, settings: MCPSettings) -> None:
        self._settings = settings

    @property
    def active(self) -> bool:
        """是否有任何护栏规则生效（无规则时可跳过检查）"""
        return bool(self._settings.tool_role_allowlist or self._settings.tool_amount_limits)

    def check(self, tool_name: str, arguments: dict[str, Any], *, actor_role: str) -> GuardDecision:
        """执行前校验：先授权，后额度。任一不通过即拒绝。"""
        auth = self._check_authorization(tool_name, actor_role=actor_role)
        if not auth.allowed:
            return auth
        return self._check_amount(tool_name, arguments)

    # ── 授权 ──

    def _check_authorization(self, tool_name: str, *, actor_role: str) -> GuardDecision:
        allowlist = self._settings.tool_role_allowlist
        if not allowlist:
            # 未配置白名单 → 不做授权限制（零回归）
            return GuardDecision(allowed=True)
        # 已配置白名单 → 保守策略：角色未登记视为无任何权限
        allowed_tools = allowlist.get(actor_role, [])
        if tool_name in allowed_tools:
            return GuardDecision(allowed=True)
        return GuardDecision(
            allowed=False,
            code="role_denied",
            reason=f"角色 {actor_role} 无权调用工具 {tool_name}",
        )

    # ── 额度 ──

    def _check_amount(self, tool_name: str, arguments: dict[str, Any]) -> GuardDecision:
        limits = self._settings.tool_amount_limits
        if tool_name not in limits:
            return GuardDecision(allowed=True)
        limit = limits[tool_name]
        for key in self._settings.amount_arg_keys:
            if key not in arguments:
                continue
            value = _coerce_number(arguments[key])
            if value is not None and value > limit:
                return GuardDecision(
                    allowed=False,
                    code="amount_exceeded",
                    reason=f"工具 {tool_name} 入参 {key}={value} 超过上限 {limit}",
                )
        return GuardDecision(allowed=True)


def _coerce_number(value: Any) -> float | None:
    """尽力将入参转为 float，无法转换返回 None（非数值不参与额度校验）"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int | float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None
