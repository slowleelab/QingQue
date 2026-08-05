"""Prometheus 指标定义与 /metrics 端点

提供请求计数、请求耗时直方图等基础指标，
以及会话生命周期指标（转换次数、停留时长、超时触发率）。
两个 FastAPI 服务共用。
"""

from __future__ import annotations

import re
import time

from prometheus_client import REGISTRY, Counter, Gauge, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response

# ── HTTP 指标 ──

REQUEST_COUNT = Counter(
    "http_requests_total",
    "HTTP 请求总数",
    ["method", "endpoint", "status"],
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP 请求耗时（秒）",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# ── 会话生命周期指标 ──

SESSION_TRANSITIONS = Counter(
    "session_transitions_total",
    "会话状态转换次数",
    ["from_phase", "from_sub", "to_phase", "to_sub", "reason"],
)

SESSION_TIMEOUTS = Counter(
    "session_timeouts_total",
    "会话超时触发次数",
    ["sub_phase", "reason"],
)

SESSION_PHASE_DURATION = Histogram(
    "session_phase_duration_seconds",
    "会话各子阶段停留时长（秒）",
    ["sub_phase"],
    buckets=[5, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600],
)

# ── 工具调用指标 ──

TOOL_CALLS = Counter(
    "tool_calls_total",
    "MCP 工具调用次数",
    ["tool", "status"],  # status: success/error
)

TOOL_CONFIRMATIONS = Counter(
    "tool_confirmations_total",
    "敏感工具确认决策次数",
    ["decision"],  # decision: pending/confirm/cancel/unclear/expired
)

TOOL_GUARD_DENIALS = Counter(
    "tool_guard_denials_total",
    "被工具护栏拦截（授权/额度）的工具调用次数",
    ["tool", "reason"],  # reason: role_denied/amount_exceeded
)

# ── LLM 调用指标 ──

LLM_CALL_DURATION = Histogram(
    "llm_call_duration_seconds",
    "LLM 调用耗时（秒）",
    ["model", "method"],  # method: chat/chat_with_tools
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 60.0],
)

# ── Bot 运行时指标（由 router 监控循环周期性刷新） ──

BOT_FAST_REPLY = Counter(
    "lumio_fast_reply_total",
    "Bot 快速兜底回复次数（并发满载时的模板回复）",
)

BOT_AGENT_RESPONSES = Counter(
    "lumio_agent_responses_total",
    "Bot Agent 正常回复次数",
    ["source"],  # source: llm/template/fallback/tool_* 等
)

# P0-5: 死信队列指标 — 消息进入死信 (处理失败/重试超限) 与人工重放
DEAD_LETTER_WRITES = Counter(
    "lumio_dead_letter_writes_total",
    "消息进入死信队列次数",
    ["reason"],  # reason: retry_exhausted / agent_error
)

DEAD_LETTER_REPLAYS = Counter(
    "lumio_dead_letter_replays_total",
    "死信消息人工重放次数",
)

BOT_SEMAPHORE_UTILIZATION = Gauge(
    "lumio_bot_semaphore_utilization",
    "Bot Agent 信号量利用率（0~1，已占用槽位 / 总槽位）",
)

BOT_ACTIVE_WORKERS = Gauge(
    "lumio_active_workers",
    "Bot 当前活跃的会话 worker 数",
)

BOT_STREAM_LENGTH = Gauge(
    "lumio_stream_length",
    "Bot 聊天消息流（Redis Stream）长度",
)

BOT_STREAM_PENDING = Gauge(
    "lumio_stream_pending_total",
    "Bot 聊天消息流待确认（PEL pending）消息数",
)

# ── RAG 检索 + 降级状态指标（commit 6b 补 dashboard 缺口） ──

RETRIEVE_DURATION = Histogram(
    "lumio_retrieval_duration_seconds",
    "RAG 检索端到端耗时（秒），含 BM25/向量/混合各路径",
    ["search_type"],  # search_type: hybrid/bm25/vector
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0],
)

DEGRADATION_LEVEL = Gauge(
    "lumio_degradation_level",
    "系统降级等级（0=normal, 1=degraded, 2=fallback），由 DegradationManager 写入",
)

# ── KV Cache 指标 (A0: 推理引擎 K/V tensor 命中率监控) ──

KV_CACHE_HIT_RATE = Gauge(
    "llm_kv_cache_hit_rate",
    "LLM KV cache 命中率（0~1），按 cache 层分桶",
    ["cache_layer"],  # cache_layer: static_prefix / semi_static / dynamic
)

PREFILL_TOKENS_SAVED = Counter(
    "llm_prefill_tokens_saved_total",
    "KV cache 命中节省的 prefill token 数",
    ["cache_layer", "model"],
)

TTFT_IMPROVEMENT = Histogram(
    "llm_ttft_improvement_ratio",
    "TTFT 优化前后对比（ratio = optimized / original），< 1 表示更快",
    ["model"],
    buckets=[0.1, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 5.0],
)

# ── 上下文压缩指标 (A1) ──

CONTEXT_COMPRESSION_RATIO = Histogram(
    "lumio_context_compression_ratio",
    "上下文压缩比（原始 token / 压缩后 token），越大压缩越狠",
    ["algorithm"],
    buckets=[1.0, 2.0, 3.0, 5.0, 8.0, 10.0, 15.0, 20.0],
)

CONTEXT_COMPRESSION_LATENCY = Histogram(
    "lumio_context_compression_latency_seconds",
    "上下文压缩耗时（秒）",
    ["algorithm"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

# ── 提示词注入防御指标 (A3-A5) ──

INJECTION_ATTEMPTS = Counter(
    "lumio_injection_attempts_total",
    "提示词注入尝试次数（含 RAG 污染 / tool 返回污染）",
    ["layer", "pattern"],  # layer: user_input / rag_content / tool_result
)

INJECTION_BLOCKED = Counter(
    "lumio_injection_blocked_total",
    "提示词注入拦截次数",
    ["layer", "action"],  # action: rejected / sanitized / quarantined
)

# ── Token 成本指标 (E0) ──

LLM_TOKEN_USAGE = Counter(
    "llm_token_usage_total",
    "LLM token 消耗",
    ["model", "method", "direction"],  # direction: input / output
)

LLM_COST_USD = Counter(
    "llm_cost_usd_total",
    "LLM 美元成本（按模型 + tenant 归因）",
    ["model", "tenant_id"],
)

LLM_BUDGET_REMAINING = Gauge(
    "llm_budget_remaining_usd",
    "LLM 月度预算剩余（美元）",
    ["tenant_id"],  # tenant_id: __default__ 表示全局
)

LLM_BUDGET_EXCEEDED = Counter(
    "llm_budget_exceeded_total",
    "LLM 预算超限拒绝次数",
    ["tenant_id", "scope"],  # scope: monthly / daily_tenant
)

# ── Agent 自反思指标 (C2) ──

AGENT_REFLECTION = Counter(
    "lumio_agent_reflection_total",
    "Agent 自我反思次数",
    ["decision"],  # decision: retry / switch_tool / escalate / pass
)

AGENT_PLAN_STEPS = Histogram(
    "lumio_agent_plan_steps",
    "Planner 拆解任务得到的步骤数",
    buckets=[1, 2, 3, 5, 7, 10, 15, 20],
)

# ── 流式响应指标 (F0) ──

LLM_STREAM_TTFT = Histogram(
    "llm_stream_ttft_seconds",
    "流式 LLM 首 token 延迟（秒）",
    ["model"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0],
)

LLM_STREAM_CHUNKS = Counter(
    "llm_stream_chunks_total",
    "流式 LLM 推送的 chunk 数",
    ["model"],
)

LLM_STREAM_CANCELLED = Counter(
    "llm_stream_cancelled_total",
    "客户端取消流式响应的次数",
    ["reason"],  # reason: client_disconnect / user_cancel / timeout
)

# ── 工具健壮性指标 (F1) ──

TOOL_CALL_DURATION = Histogram(
    "tool_call_duration_seconds",
    "工具调用耗时（秒）",
    ["tool_name", "status"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0],
)

TOOL_RETRIES = Counter(
    "tool_retries_total",
    "工具调用重试次数",
    ["tool_name", "reason"],
)

TOOL_QUOTA_EXCEEDED = Counter(
    "tool_quota_exceeded_total",
    "工具调用超配额拒绝次数",
    ["tool_name", "scope"],  # scope: customer_window / tenant_window
)

MCP_RECONNECT_ATTEMPTS = Counter(
    "mcp_reconnect_attempts_total",
    "MCP 后端自动重连尝试次数",
    ["server_name", "result"],  # result: success / failed
)

# ── A/B 实验指标 ──

AB_EXPERIMENT_EXPOSURES = Counter(
    "lumio_ab_experiments_total",
    "A/B 实验曝光次数",
    ["experiment", "variant"],
)

AB_EXPERIMENT_CONVERSIONS = Counter(
    "lumio_ab_experiment_conversions_total",
    "A/B 实验转化次数",
    ["experiment", "variant", "goal"],
)

# ── 评估闭环指标 (B3) ──

EVAL_JUDGE_SCORE = Histogram(
    "lumio_eval_judge_score",
    "LLM-as-Judge 评估分数（1~5）",
    ["dimension", "model"],
    buckets=[1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0],
)

EVAL_REGRESSION_PASS_RATE = Gauge(
    "lumio_eval_regression_pass_rate",
    "Prompt 回归测试通过率（0~1）",
    ["golden_set_version"],
)

BAD_CASE_MARKED = Counter(
    "lumio_bad_case_marked_total",
    "坐席标记的差评案例数",
    ["reason"],
)

# 排除自采集，避免 Prometheus 抓取 /metrics 产生反馈循环
_EXCLUDED_PATHS = {"/metrics", "/health", "/favicon.ico"}

# P2-1 第五轮修复: path 模板化 (高基数防护)
# 8+ 字符且含数字/连字符的段 (UUID/session_id/长数字) 归一化为 {param};
# 纯字母短词 (sessions/chat/send/health) 不受影响
_PATH_PARAM_RE = re.compile(r"/(?=[0-9a-zA-Z-]*[0-9-])[0-9a-zA-Z-]{8,}")


def _normalize_metric_path(path: str) -> str:
    """归一化请求 path 为低基数 label (路由模板形态)."""
    return _PATH_PARAM_RE.sub("/{param}", path)


async def metrics_endpoint(request: Request) -> Response:
    """暴露 /metrics 供 Prometheus 采集"""
    output = generate_latest(REGISTRY)
    return Response(content=output, media_type="text/plain; version=0.0.4; charset=utf-8")


class PrometheusMiddleware:
    """Starlette 中间件，采集每个请求的计数和耗时"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        # P2-1 第五轮修复: 指标高基数 — 旧方案 scope["route"] 在纯 ASGI 中间件执行
        # 时恒为 None (路由匹配晚于中间件), 回退原始 path → /api/sessions/{uuid}/messages
        # 每会话一个时间序列. 改为 path 模板化正则归一化 (UUID/长数字 → {param}).
        endpoint = _normalize_metric_path(path)

        # 排除指标端点自身，避免反馈循环
        if path in _EXCLUDED_PATHS:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        start = time.perf_counter()
        status_code = 200

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 200)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - start
            REQUEST_COUNT.labels(method=method, endpoint=endpoint, status=status_code).inc()
            REQUEST_LATENCY.labels(method=method, endpoint=endpoint).observe(duration)
