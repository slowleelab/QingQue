"""参考 MCP Server（信用卡示例工具）

用 :class:`FastMCP` 暴露一组代表性的信用卡自助工具，返回 mock 数据，用于：

1. 本地联调：``python -m smartcs.services.tools.reference_server`` 起一个
   streamable-http MCP 服务，配合 ``MCP_ENABLED=True`` +
   ``MCP_ENDPOINT=http://127.0.0.1:8080/mcp`` 即可让 bot 真实调用工具。
2. 集成测试：``build_reference_server()`` 可经进程内内存传输接入
   ``MCPToolClient.use_session()``，无需网络即可端到端验证工具循环。

敏感性约定（与 :class:`MCPToolClient` 的判定一致）：
- 只读查询类工具标注 ``readOnlyHint=True``，非敏感，直接执行。
- 写操作 / 资金相关工具标注 ``destructiveHint=True``，敏感，触发确认状态机。

红线：这些工具返回 mock 数据，**不对接真实银行核心系统**，仅供演示与测试。
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

# ── mock 数据（仅演示，不含真实持卡人信息）──
_MOCK_BILLS: dict[str, dict[str, Any]] = {
    "default": {"total_amount": 3288.50, "min_payment": 328.85, "due_date": "2026-08-15", "currency": "CNY"},
}
_MOCK_POINTS: dict[str, int] = {"default": 128600}


def build_reference_server(
    *,
    name: str = "smartcs-reference-tools",
    host: str = "127.0.0.1",
    port: int = 8080,
) -> FastMCP:
    """构造参考 MCP Server 实例

    Args:
        name: 服务名（在 MCP 握手时上报）
        host: streamable-http 监听地址（仅在作为独立服务运行时生效）
        port: streamable-http 监听端口

    Returns:
        已注册全部示例工具的 :class:`FastMCP` 实例
    """
    mcp = FastMCP(name=name, host=host, port=port, stateless_http=True)

    @mcp.tool(
        description="查询信用卡账单：返回本期应还金额、最低还款额与还款日",
        annotations=ToolAnnotations(title="查账单", readOnlyHint=True, destructiveHint=False),
    )
    def query_card_bill(card_no: str, month: str = "") -> dict[str, Any]:
        """查询指定卡号在某账单月的账单概要"""
        bill = _MOCK_BILLS.get(card_no, _MOCK_BILLS["default"])
        return {"card_no": card_no, "month": month or "当期", **bill}

    @mcp.tool(
        description="查询信用卡积分余额",
        annotations=ToolAnnotations(title="查积分", readOnlyHint=True, destructiveHint=False),
    )
    def query_points(card_no: str) -> dict[str, Any]:
        """查询指定卡号的可用积分"""
        points = _MOCK_POINTS.get(card_no, _MOCK_POINTS["default"])
        return {"card_no": card_no, "points": points}

    @mcp.tool(
        description="查询账单分期优惠方案：给出可选期数与对应手续费率",
        annotations=ToolAnnotations(title="查分期优惠", readOnlyHint=True, destructiveHint=False),
    )
    def query_installment_offer(card_no: str, amount: float) -> dict[str, Any]:
        """按金额返回可选的账单分期期数与费率（mock）"""
        offers = [
            {"periods": 3, "fee_rate": 0.023, "monthly": round(amount * (1 + 0.023) / 3, 2)},
            {"periods": 6, "fee_rate": 0.045, "monthly": round(amount * (1 + 0.045) / 6, 2)},
            {"periods": 12, "fee_rate": 0.086, "monthly": round(amount * (1 + 0.086) / 12, 2)},
        ]
        return {"card_no": card_no, "amount": amount, "offers": offers}

    @mcp.tool(
        description="办理账单分期：将指定金额按选定期数分期（资金操作，需客户确认）",
        annotations=ToolAnnotations(title="账单分期办理", readOnlyHint=False, destructiveHint=True),
    )
    def apply_bill_installment(card_no: str, amount: float, periods: int) -> dict[str, Any]:
        """办理账单分期（mock）：返回受理单号与预计生效状态"""
        return {
            "card_no": card_no,
            "amount": amount,
            "periods": periods,
            "status": "accepted",
            "application_no": f"INST{periods:02d}{int(amount)}",
            "message": f"已受理 {periods} 期账单分期，金额 {amount} 元",
        }

    @mcp.tool(
        description="申请临时提升信用额度（资金相关，需客户确认）",
        annotations=ToolAnnotations(title="临时提额", readOnlyHint=False, destructiveHint=True),
    )
    def adjust_temp_credit_limit(card_no: str, target_limit: float) -> dict[str, Any]:
        """申请临时提额（mock）：返回受理状态与有效期"""
        return {
            "card_no": card_no,
            "target_limit": target_limit,
            "status": "accepted",
            "valid_until": "2026-09-30",
            "message": f"已受理临时提额至 {target_limit} 元的申请",
        }

    return mcp


def main() -> None:
    """作为独立进程运行参考 MCP Server（streamable-http 传输）

    环境变量：
    - ``MCP_REF_HOST``（默认 ``127.0.0.1``）
    - ``MCP_REF_PORT``（默认 ``8080``）

    默认对外入口路径为 ``/mcp``，即 ``http://<host>:<port>/mcp``。
    """
    host = os.getenv("MCP_REF_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_REF_PORT", "8080"))
    server = build_reference_server(host=host, port=port)
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
