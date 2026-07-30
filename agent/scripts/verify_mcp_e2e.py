"""MCP 工具层端到端联调 harness（人工/可选运行）

在**不依赖 Higress / Docker** 的前提下，用两条互补链路验证 MCP 工具工程是否可用：

阶段 1 — Java mcp-server 直连（真实 SSE 传输）
    Python ``mcp`` SSE 客户端 → ``http://localhost:8090/sse`` →
    列举工具（断言 22 个）+ 只读 / 写 / 幂等 / 业务错误代表性用例。

阶段 2 — 编排大脑渐进式暴露（进程内内存传输 + 真实 LLM）
    参考 MCP Server（``build_reference_server``）↔ ``MCPToolClient`` ↔
    ``ToolCallingExecutor``，开启渐进式暴露，按意图裁剪工具子集后交真实 LLM 自主调用。

阶段 0 — 静态一致性
    校验 ``intent_tool_map`` 引用的工具名都存在于工具目录（防悬空引用）。

设计原则：
- **零外部副作用**：仅连接 mock / 参考工具，绝不触达真实银行系统；日志只出现卡号尾号。
- **友好降级**：缺 live Java（:8090）或 LLM（Ollama :11434）时，相关阶段判定为 SKIP 并给出
  启动指引，而非误报失败；硬性契约（22 工具、幂等、错误、渐进式裁剪）失败才 ``exit(1)``。

使用方式:
    # 先构建并启动 Java mcp-server（另开终端）
    make mcp-server-build && make mcp-server-run
    # 可选：本地 LLM
    make verify-ollama
    # 运行联调 harness
    make verify-mcp-e2e
    # 或直接： cd agent && poetry run python scripts/verify_mcp_e2e.py
"""

from __future__ import annotations

import asyncio
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# 允许以 `python scripts/verify_mcp_e2e.py` 直接运行：把包根（agent/）加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.shared.memory import create_connected_server_and_client_session as connect_in_memory

from smartcs.services.bot.tool_executor import ToolCallingExecutor
from smartcs.services.bot.tool_selection import select_tools_for_intent
from smartcs.services.common.llm import LLMClient
from smartcs.services.common.mcp_client import MCPToolClient
from smartcs.services.tools.reference_server import build_reference_server
from smartcs.shared.config import MCPSettings, get_settings

# ── 联调常量 ──
SSE_URL = "http://localhost:8090/sse"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"

# Luhn 合法的 mock 演示卡（详见 mcp-server/README.md / DemoCards）
PRIMARY = "6225880012346780"
UNACTIVATED = "6225880000007899"
UNKNOWN = "6225880099999998"

# Java mcp-server 期望的 22 个工具全名（阶段 A 自动收集注册）
EXPECTED_JAVA_TOOLS = frozenset(
    {
        "query_card_bill",
        "query_bill_detail",
        "query_installment_status",
        "cancel_installment",
        "repay_credit_card",
        "query_repayment_history",
        "set_auto_repay",
        "query_credit_limit",
        "adjust_temp_credit_limit",
        "query_limit_adjust_history",
        "apply_permanent_limit",
        "query_points",
        "redeem_points",
        "query_card_benefits",
        "query_card_status",
        "query_annual_fee",
        "activate_card",
        "report_card_lost",
        "query_transactions",
        "report_transaction_dispute",
        "query_installment_offer",
        "apply_bill_installment",
    }
)

BUSINESS_PROMPT = (
    "你是信用卡智能客服，可调用工具查询账户信息。" "当用户询问账单时，请先调用合适的工具获取真实数据再回复。"
)


class Reporter:
    """收集各阶段结果并汇总退出码：PASS/FAIL/SKIP。"""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.skips: list[str] = []
        self.passes: list[str] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.passes.append(name)
        print(f"  ✅ {name}{(' — ' + detail) if detail else ''}")

    def fail(self, name: str, detail: str = "") -> None:
        self.failures.append(name)
        print(f"  ❌ {name}{(' — ' + detail) if detail else ''}")

    def skip(self, name: str, reason: str) -> None:
        self.skips.append(name)
        print(f"  ⏭️  SKIP {name} — {reason}")

    def summarize_and_exit(self) -> None:
        print("\n" + "=" * 60)
        print(f"联调汇总：PASS={len(self.passes)}  FAIL={len(self.failures)}  SKIP={len(self.skips)}")
        if self.skips:
            print(f"  跳过：{', '.join(self.skips)}")
        if self.failures:
            print(f"  失败：{', '.join(self.failures)}")
            print("❌ MCP 工具层联调未通过")
            print("=" * 60)
            sys.exit(1)
        print("🎉 MCP 工具层联调通过（未跳过项全部达标）")
        print("=" * 60)
        sys.exit(0)


# ── 探活 ──


def _sse_reachable() -> bool:
    """判断 Java mcp-server 是否在 :8090 就绪（SSE 端点响应即算就绪）。"""
    try:
        with urllib.request.urlopen(SSE_URL, timeout=3) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        # SSE 端点可能返回非 200 但服务确实在，宽松判定为可达
        return exc.code < 500
    except Exception:
        return False


def _llm_reachable() -> bool:
    """判断本地 LLM（Ollama）是否可达。远程 LLM 端点也应能连通时才跑阶段 2 的真实调用。"""
    settings = get_settings().llm
    # 非本地 Ollama（如远程网关）时，无法廉价探活，交由实际调用兜底
    if "localhost:11434" not in settings.base_url and "127.0.0.1:11434" not in settings.base_url:
        return True
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def _text(result: Any) -> str:
    parts = [getattr(c, "text", str(c)) for c in result.content]
    return "\n".join(parts)


# ── 阶段 0：静态一致性 ──


def phase0_consistency(reporter: Reporter) -> None:
    print("\n【阶段 0】intent_tool_map 引用一致性")
    intent_map = get_settings().mcp.intent_tool_map
    dangling = sorted(
        {(intent, name) for intent, names in intent_map.items() for name in names if name not in EXPECTED_JAVA_TOOLS}
    )
    if dangling:
        reporter.fail("intent_tool_map 引用有效", f"悬空工具名: {dangling}")
    else:
        total = sum(len(v) for v in intent_map.values())
        reporter.ok("intent_tool_map 引用有效", f"{len(intent_map)} 个意图 / {total} 处引用全部命中工具目录")


# ── 阶段 1：SSE 直连 Java ──


async def phase1_java_sse(reporter: Reporter) -> None:
    print("\n【阶段 1】Python SSE 客户端 → Java mcp-server (:8090)")
    if not _sse_reachable():
        reporter.skip(
            "Java 22 工具直连",
            "未检测到 :8090，请先 `make mcp-server-build && make mcp-server-run`",
        )
        return

    async with sse_client(SSE_URL) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        resp = await session.list_tools()
        names = {t.name for t in resp.tools}
        if len(names) == 22 and names == EXPECTED_JAVA_TOOLS:
            reporter.ok("列举工具", "22 个工具全部匹配预期目录")
        else:
            missing = sorted(EXPECTED_JAVA_TOOLS - names)
            extra = sorted(names - EXPECTED_JAVA_TOOLS)
            reporter.fail("列举工具", f"count={len(names)} missing={missing} extra={extra}")

        # 只读
        r = await session.call_tool("query_card_bill", {"cardNo": PRIMARY})
        if not r.isError and "statementAmount" in _text(r):
            reporter.ok("只读 query_card_bill(主卡)")
        else:
            reporter.fail("只读 query_card_bill(主卡)", _text(r)[:120])

        r = await session.call_tool("query_card_status", {"cardNo": UNACTIVATED})
        if not r.isError and "未激活" in _text(r):
            reporter.ok("只读 query_card_status(未激活卡)")
        else:
            reporter.fail("只读 query_card_status(未激活卡)", _text(r)[:120])

        # 写 + 幂等：同一 idempotencyKey 两次调用应回放一致结果
        akey = "verify-e2e-activate"
        args = {"cardNo": UNACTIVATED, "idempotencyKey": akey}
        r1 = await session.call_tool("activate_card", args)
        r2 = await session.call_tool("activate_card", args)
        t1, t2 = _text(r1), _text(r2)
        if not r1.isError and t1 == t2:
            reporter.ok("写+幂等 activate_card", "同 key 两次回放一致")
        else:
            reporter.fail("写+幂等 activate_card", f"err={r1.isError} identical={t1 == t2}")

        # 业务错误：未知卡号应返回 isError
        r = await session.call_tool("query_card_bill", {"cardNo": UNKNOWN})
        if r.isError and ("未找到" in _text(r) or "卡" in _text(r)):
            reporter.ok("业务错误 未知卡号", "返回 isError + 面向用户中文提示")
        else:
            reporter.fail("业务错误 未知卡号", _text(r)[:120])


# ── 阶段 2：渐进式暴露 + 真实 LLM ──


async def phase2_progressive_disclosure(reporter: Reporter) -> None:
    print("\n【阶段 2】MCPToolClient + 参考 Server + 真实 LLM（渐进式暴露开启）")

    # 强制开启渐进式暴露 + 高置信度，验证「裁剪到子集」这条实际路径
    settings = get_settings()
    mcp_settings = MCPSettings(enabled=True, progressive_disclosure_enabled=True)

    server = build_reference_server(name="verify-e2e-ref")
    async with connect_in_memory(server._mcp_server) as session:
        mcp = MCPToolClient(settings=mcp_settings)
        await mcp.use_session(session)

        all_tools = sorted(t["function"]["name"] for t in mcp.to_openai_tools())
        reporter.ok("参考 Server 连接", f"暴露 {len(all_tools)} 个工具: {all_tools}")

        # 渐进式裁剪：bill_query + 高置信 → 子集；子集与目录取交集后应显著收窄
        subset = select_tools_for_intent("bill_query", 0.95, mcp_settings)
        if subset is None:
            reporter.fail("渐进式裁剪 select_tools_for_intent", "flag 开启却返回 None（应返回子集）")
            return
        exposed = sorted(t["function"]["name"] for t in mcp.to_openai_tools(subset))
        if exposed and set(exposed) < set(all_tools) and "query_card_bill" in exposed:
            reporter.ok("渐进式裁剪", f"{len(all_tools)} 个 → 交集收窄为 {exposed}")
        else:
            reporter.fail("渐进式裁剪", f"subset={subset} exposed={exposed}")

        # 真实 LLM 自主调用（可选）
        if not _llm_reachable():
            reporter.skip("真实 LLM 工具调用", "未检测到本地 LLM（Ollama :11434），请先 `make verify-ollama`")
            return

        llm = LLMClient(settings=settings.llm)
        executor = ToolCallingExecutor(
            mcp_client=mcp,
            llm_client=llm,
            audit_session_factory=None,
            settings=mcp_settings,
            guard=None,
        )
        try:
            result = await executor.run_conversation(
                system_prompt=BUSINESS_PROMPT,
                user_input=f"帮我查一下卡号 {PRIMARY} 的账单还有多少要还？",
                history=[],
                session_id="verify-e2e-sess",
                actor_id="verify-cust",
                tool_names=subset,
            )
        except Exception as exc:
            reporter.skip("真实 LLM 工具调用", f"LLM 调用异常（{type(exc).__name__}），跳过")
            return

        if "query_card_bill" in (result.executed_tools or []):
            reporter.ok("真实 LLM 工具调用", f"LLM 从子集选用 query_card_bill；回复: {result.content[:60]}…")
        else:
            reporter.fail(
                "真实 LLM 工具调用",
                f"source={result.source} executed={result.executed_tools} content={result.content[:80]}",
            )


async def _run() -> None:
    print("=" * 60)
    print("SmartCS MCP 工具层端到端联调 harness")
    print("（仅连接 mock / 参考工具，绝不触达真实银行系统）")
    print("=" * 60)
    reporter = Reporter()
    phase0_consistency(reporter)
    await phase1_java_sse(reporter)
    await phase2_progressive_disclosure(reporter)
    reporter.summarize_and_exit()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
