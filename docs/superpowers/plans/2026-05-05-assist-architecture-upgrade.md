# AI辅助功能架构升级 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按照架构设计文档严格实现六层架构：输入感知 → 统一状态 → 宏观编排(Temporal) → 微观执行(LangGraph DAG + Activity) → 仲裁输出 → 反馈闭环

**Architecture:** 宏观编排层使用 Temporal Workflow 驱动 OE 状态机，三路评估器(D1/D2/D3)和三路执行器(E1/E2/E3)作为 Temporal Activity；AI 服务执行器(E1)内嵌 LangGraph DAG 实现快速/深度双通路推理；统一状态层使用 Redis CAS 乐观锁；仲裁层实现优先级融合规则 + PII 脱敏；反馈层实现隐式反馈收集。

**Tech Stack:** Python 3.11, FastAPI, Temporal Python SDK, LangGraph, Redis (CAS Lua), asyncio, Pydantic v2, pytest + pytest-asyncio

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `agent/smartcs/services/common/circuit_breaker.py` | 通用熔断器（已有，Task 1 已完成） |
| `agent/smartcs/services/common/state_manager.py` | CAS 乐观锁状态管理器（Redis Lua 脚本、覆写规则） |
| `agent/smartcs/workflows/__init__.py` | Temporal workflows 包 |
| `agent/smartcs/workflows/orchestration_workflow.py` | OE Temporal Workflow（状态机驱动） |
| `agent/smartcs/workflows/activities.py` | 6 个 Temporal Activity（D1/D2/D3 评估 + E1/E2/E3 执行） |
| `agent/smartcs/workflows/shared.py` | Workflow/Activity 共享数据模型 |
| `agent/smartcs/services/assist/ai_executor_dag.py` | E1 LangGraph DAG（快速/深度双通路 + 合规防火墙） |
| `agent/smartcs/services/assist/arbitrator.py` | 全局仲裁器（优先级融合 + PII 脱敏） |
| `agent/smartcs/workflows/worker.py` | Temporal Worker 启动入口 |
| `agent/smartcs/workflows/temporal_client.py` | Temporal Client 连接管理 |
| `agent/tests/test_state_manager.py` | State manager 单元测试 |
| `agent/tests/test_ai_executor_dag.py` | LangGraph DAG 单元测试 |
| `agent/tests/test_arbitrator.py` | 仲裁器单元测试 |
| `agent/tests/test_workflow.py` | Temporal Workflow 单元测试 |

### Modified Files

| File | Changes |
|------|---------|
| `agent/smartcs/shared/models.py` | 新增 EmotionVector, ExecutorResult, ArbitrationResult, OrchestrationState, FeedbackSignal, RiskActionEnum, OEState；扩展 SessionState |
| `agent/smartcs/shared/config.py` | 新增 OrchestrationSettings, TemporalSettings, CircuitBreakerConfigSettings |
| `agent/smartcs/shared/exceptions.py` | 新增 CircuitBreakerOpenError(4020), StateConflictError(5003), OrchestrationTimeoutError(5004) |
| `agent/smartcs/shared/orm_models.py` | 新增 OrchestrationLog, FeedbackLog ORM 模型 |
| `agent/smartcs/services/common/deps.py` | 新增 StateManager, TemporalClient, Worker 的 init/close/get |
| `agent/smartcs/services/assist/router.py` | 替换为 Temporal Workflow 触发，新增 /feedback 端点 |
| `agent/smartcs/main.py` | 更新 assist 启动步骤，加入 Temporal 初始化 |
| `agent/pyproject.toml` | 新增 temporalio 依赖 |
| `deploy/docker-compose.yml` | 新增 Temporal Server + UI 服务 |

---

## Task 1: Generic Circuit Breaker ✅ COMPLETED

已实现 `agent/smartcs/services/common/circuit_breaker.py` + `CircuitBreakerOpenError`(4020)。36 个测试全部通过。

---

## Task 2: Extended State Models + Configuration

**Files:**
- Modify: `agent/smartcs/shared/models.py`
- Modify: `agent/smartcs/shared/orm_models.py`
- Modify: `agent/smartcs/shared/config.py`
- Modify: `agent/smartcs/shared/exceptions.py`
- Test: `agent/tests/test_state_models.py` (new)
- Test: `agent/tests/test_config_extensions.py` (new)

- [ ] **Step 1: Write failing tests for state models**

Add to models.py:
- `RiskActionEnum` (PASS/WARN/BLOCK)
- `OEState` (IDLE/EVALUATING/DISPATCHING/WAITING_RESULTS/COMPLETED)
- `EmotionVector` (label, score, decay_lambda=0.005, updated_at, decayed_score method)
- `ExecutorResult` (executor_id, ui_schema, latency_ms, success, degraded, degradation_type, risk_action, trace_id)
- `ArbitrationResult` (primary_card, risk_badge, marketing_slot, fusion_type, trace_id)
- `OrchestrationState` (session_id, oe_state, d1/d2/d3_activated, cooldowns, activation_history, global_timeout_ms)
- `FeedbackSignal` (session_id, agent_id, action, confidence, modify_fields, timestamp)
- Extend `SessionState` with: intent_stack, entity_pool, emotion_vector, suppress_flag, node_position, risk_pending_audit

Add to config.py:
- `OrchestrationSettings` (d1/d2/d3 cooldown, thresholds, global_timeout, E1/E2/E3 SLA, marketing_defer_ms)
- `TemporalSettings` (host, port, namespace, task_queue, workflow_timeout)
- `CircuitBreakerConfigSettings` (per-executor: ai/mkt/risk failure_rate, slow_call_rate, slow_call_duration, wait_duration_open, sliding_window_size)

Add to exceptions.py:
- `StateConflictError(5003)`, `OrchestrationTimeoutError(5004)`

Add to orm_models.py:
- `OrchestrationLog`, `FeedbackLog`

- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement models, config, exceptions, ORM**
- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit**

```bash
git add agent/smartcs/shared/ agent/tests/test_state_models.py agent/tests/test_config_extensions.py
git commit -m "feat: add orchestration state models, Temporal config, and extended session state"
```

---

## Task 3: CAS State Manager

**Files:**
- Create: `agent/smartcs/services/common/state_manager.py`
- Test: `agent/tests/test_state_manager.py`

严格按照文档 §3.2 实现：

- [ ] **Step 1: Write failing tests**

```python
# Test: read_state, cas_write (version match → success, mismatch → conflict with retry),
# merge rules: full_overwrite(risk_pending_audit, node_position),
# incremental_merge(intent_stack, entity_pool),
# time_window_replace(emotion_vector),
# one_way_gate(suppress_flag: false→true only),
# get_snapshot (versioned), init_state
```

- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement StateManager with Redis Lua CAS script**

Lua 脚本严格按文档：
```
EVALSHA cas_write.lua
  -- 参数: session_id, expected_version, patch_fields
  -- current = GET state:{session_id}
  -- IF current.version != expected_version THEN RETURN {ok: false, current_version: current.version}
  -- current.version += 1
  -- current.last_writer = executor_id
  -- current.updated_at = NOW()
  -- MERGE patch_fields INTO current  -- 字段级合并
  -- SET state:{session_id} = current
  -- RETURN {ok: true, new_version: current.version}
```

覆写规则严格按文档表：
| 字段类型 | 合并策略 | 仲裁者 |
|----------|----------|--------|
| 风控指令 | 全量覆写 | 风控执行器 |
| 意图栈 | 增量合并 | AI 服务执行器 |
| 实体池 | 增量合并 | AI 服务执行器 |
| 情绪向量 | 时间窗口替换 | 任意写入者 |
| Suppress_Flag | 只能 false→true | 营销评估器 |
| Node_Position | 全量覆写 | AI 服务执行器 |

- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit**

```bash
git add agent/smartcs/services/common/state_manager.py agent/tests/test_state_manager.py
git commit -m "feat: add CAS optimistic lock state manager with merge rules per spec"
```

---

## Task 4: Temporal Infrastructure

**Files:**
- Modify: `agent/pyproject.toml` (add temporalio)
- Modify: `deploy/docker-compose.yml` (add Temporal Server + UI)
- Create: `agent/smartcs/workflows/__init__.py`
- Create: `agent/smartcs/workflows/shared.py` (shared data models for workflow/activity)
- Create: `agent/smartcs/workflows/temporal_client.py` (Temporal Client connection management)
- Create: `agent/smartcs/workflows/worker.py` (Worker startup)
- Test: `agent/tests/test_temporal_client.py`

- [ ] **Step 1: Add temporalio dependency**

```bash
cd agent && poetry add temporalio
```

- [ ] **Step 2: Add Temporal to docker-compose.yml**

```yaml
  # ── Temporal Server ─────────────────────────────────
  temporal:
    image: temporalio/auto-setup:latest
    container_name: smartcs-temporal
    restart: unless-stopped
    environment:
      - DB=postgresql
      - DB_PORT=5432
      - POSTGRES_USER=smartcs
      - POSTGRES_PWD=${POSTGRES_PASSWORD_DOCKER:-smartcs_pass}
      - POSTGRES_SEEDS=postgres
      - DYNAMIC_CONFIG_FILE_PATH=config/dynamicconfig/development-sql.yaml
    ports:
      - "7233:7233"
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - smartcs-net

  temporal-ui:
    image: temporalio/ui:latest
    container_name: smartcs-temporal-ui
    restart: unless-stopped
    environment:
      - TEMPORAL_ADDRESS=temporal:7233
      - TEMPORAL_CORS_ORIGINS=*
    ports:
      - "8085:8080"
    depends_on:
      - temporal
    networks:
      - smartcs-net
```

- [ ] **Step 3: Create shared workflow data models**

```python
# agent/smartcs/workflows/shared.py
"""Temporal Workflow/Activity 共享数据模型"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from smartcs.shared.models import RiskActionEnum


@dataclass
class EvaluatorInput:
    """评估器输入"""
    session_id: str
    state_snapshot: dict[str, Any]


@dataclass
class EvaluatorOutput:
    """评估器输出"""
    activated: bool = False
    reason: str = ""
    cooldown_remaining: int = 0


@dataclass
class ExecutorInput:
    """执行器输入"""
    session_id: str
    message: str
    intent: str = "faq"
    sentiment: str = "neutral"
    sentiment_history: list[str] = None
    state_snapshot: dict[str, Any] = None
    trace_id: str = ""

    def __post_init__(self):
        if self.sentiment_history is None:
            self.sentiment_history = []
        if self.state_snapshot is None:
            self.state_snapshot = {}


@dataclass
class ExecutorOutput:
    """执行器输出"""
    executor_id: str = ""
    ui_schema: dict[str, Any] = None
    latency_ms: int = 0
    success: bool = True
    degraded: bool = False
    degradation_type: str = ""
    risk_action: str = ""  # RiskActionEnum value
    trace_id: str = ""

    def __post_init__(self):
        if self.ui_schema is None:
            self.ui_schema = {}


@dataclass
class OrchestrationResult:
    """编排结果（Workflow 返回值）"""
    primary_card: dict[str, Any] = None
    risk_badge: dict[str, Any] = None
    marketing_slot: dict[str, Any] = None
    fusion_type: str = "service_only"
    trace_id: str = ""
    elapsed_ms: int = 0

    def __post_init__(self):
        if self.primary_card is None:
            self.primary_card = {}
```

- [ ] **Step 4: Create Temporal client management**

```python
# agent/smartcs/workflows/temporal_client.py
"""Temporal Client 连接管理"""

from __future__ import annotations

import logging
from typing import Any

from temporalio.client import Client

from smartcs.shared.config import get_settings

logger = logging.getLogger(__name__)

_client: Client | None = None


async def get_temporal_client() -> Client:
    """获取或创建 Temporal Client 单例"""
    global _client
    if _client is None:
        settings = get_settings()
        _client = await Client.connect(
            target_host=f"{settings.temporal.host}:{settings.temporal.port}",
            namespace=settings.temporal.namespace,
        )
        logger.info("Temporal Client 连接成功: %s:%d", settings.temporal.host, settings.temporal.port)
    return _client


async def close_temporal_client() -> None:
    """关闭 Temporal Client"""
    global _client
    if _client is not None:
        await _client.close()
        _client = None
```

- [ ] **Step 5: Create Worker startup**

```python
# agent/smartcs/workflows/worker.py
"""Temporal Worker 启动"""

from __future__ import annotations

import logging

from temporalio.client import Client
from temporalio.worker import Worker

from smartcs.shared.config import get_settings
from smartcs.workflows.activities import (
    evaluate_d1_service,
    evaluate_d2_marketing,
    evaluate_d3_risk,
    execute_e1_ai_service,
    execute_e2_marketing,
    execute_e3_risk,
)
from smartcs.workflows.orchestration_workflow import OrchestrationWorkflow

logger = logging.getLogger(__name__)

_worker: Worker | None = None


async def start_worker(client: Client) -> Worker:
    """启动 Temporal Worker"""
    global _worker
    settings = get_settings()
    _worker = Worker(
        client=client,
        task_queue=settings.temporal.task_queue,
        workflows=[OrchestrationWorkflow],
        activities=[
            evaluate_d1_service,
            evaluate_d2_marketing,
            evaluate_d3_risk,
            execute_e1_ai_service,
            execute_e2_marketing,
            execute_e3_risk,
        ],
    )
    # Worker.run() 是阻塞的，需要在后台任务中执行
    logger.info("Temporal Worker 启动: task_queue=%s", settings.temporal.task_queue)
    return _worker


async def stop_worker() -> None:
    """停止 Temporal Worker"""
    global _worker
    if _worker is not None:
        # Worker shutdown 通过取消其 run task 实现
        _worker = None
```

- [ ] **Step 6: Write tests and run**
- [ ] **Step 7: Commit**

```bash
git add agent/pyproject.toml deploy/docker-compose.yml agent/smartcs/workflows/ agent/tests/test_temporal_client.py
git commit -m "feat: add Temporal infrastructure - docker, client, worker, shared models"
```

---

## Task 5: LangGraph DAG — AI 服务执行器 (E1)

**Files:**
- Create: `agent/smartcs/services/assist/ai_executor_dag.py`
- Test: `agent/tests/test_ai_executor_dag.py`

严格按照文档 §2 实现 LangGraph DAG：

```
Entry → CheckFast(Top1>0.9?)
  → 是: FastRetrieval(标准话术命中) → MonitorHit(快速通路命中记录) → Firewall
  → 否: RAG链路 + KG链路(并行) → Normalize(候选归一化) → ReRank(统一重排序) → Firewall
Firewall → 拦截: Fallback(降级安全兜底话术) → FormatOut
Firewall → 放行: FormatOut(格式化UI Schema) → Exit
```

- [ ] **Step 1: Write failing tests**

```python
class TestAIExecutorDAG:
    """LangGraph DAG 单元测试"""

    def test_dag_graph_structure(self):
        """验证 DAG 节点和边"""
        dag = AIExecutorDAG()
        graph = dag.build_graph()
        # 验证节点存在
        assert "check_fast" in graph.nodes
        assert "fast_retrieval" in graph.nodes
        assert "rag_chain" in graph.nodes
        assert "kg_chain" in graph.nodes
        assert "normalize" in graph.nodes
        assert "rerank" in graph.nodes
        assert "firewall" in graph.nodes
        assert "fallback" in graph.nodes
        assert "format_out" in graph.nodes

    @pytest.mark.asyncio
    async def test_fast_path_when_high_confidence(self):
        """Top1>0.9 时走快速通路"""
        dag = AIExecutorDAG(script_service=mock_script, ...)
        result = await dag.run(state={"confidence": 0.95, "intent": "faq", ...})
        assert result["path"] == "fast"
        assert "fast_path_hit" in result

    @pytest.mark.asyncio
    async def test_deep_path_when_low_confidence(self):
        """Top1<0.9 时走深度通路"""
        dag = AIExecutorDAG(...)
        result = await dag.run(state={"confidence": 0.6, "intent": "bill_query", ...})
        assert result["path"] == "deep"

    @pytest.mark.asyncio
    async def test_firewall_blocks_noncompliant(self):
        """合规防火墙拦截不合规内容"""
        dag = AIExecutorDAG(...)
        result = await dag.run(state={"confidence": 0.95, ...})
        # firewall should have checked compliance

    @pytest.mark.asyncio
    async def test_fallback_on_firewall_block(self):
        """防火墙拦截后降级到安全兜底话术"""
        dag = AIExecutorDAG(...)
        # 模拟 firewall 拦截
        result = await dag.run(state={...})
        assert result.get("degraded") is True or "fallback" in result

    @pytest.mark.asyncio
    async def test_normalize_balances_candidates(self):
        """候选归一化：RAG Top-5 / KG Top-3"""
        # 验证 normalize 节点将候选数量归一化

    @pytest.mark.asyncio
    async def test_rerank_uses_adoption_rate(self):
        """ReRank 特征包含语义、情绪、采纳率，无采纳率降权×0.7"""
        # 验证 rerank 节点

    @pytest.mark.asyncio
    async def test_output_format_is_ui_schema(self):
        """输出格式化为标准 UI Schema 并携带 trace_id"""
        dag = AIExecutorDAG(...)
        result = await dag.run(state={"confidence": 0.95, "trace_id": "t1", ...})
        assert "ui_schema" in result
        assert result["trace_id"] == "t1"
```

- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement LangGraph DAG**

```python
# agent/smartcs/services/assist/ai_executor_dag.py
"""AI 服务执行器 — LangGraph DAG

实现快速/深度双通路推理，严格按照设计文档 §2。
"""

from __future__ import annotations

import logging
import time
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from smartcs.services.assist.script_service import ScriptService
from smartcs.shared.models import IntentLabel, SentimentLabel

logger = logging.getLogger(__name__)


class DAGState(TypedDict, total=False):
    """DAG 内部状态"""
    session_id: str
    message: str
    intent: str
    sentiment: str
    confidence: float
    state_snapshot: dict[str, Any]
    trace_id: str
    # 通路选择
    path: str  # "fast" | "deep"
    fast_path_hit: bool
    # 快速通路
    fast_script: dict[str, Any]
    # 深度通路
    rag_candidates: list[dict[str, Any]]
    kg_candidates: list[dict[str, Any]]
    normalized_candidates: list[dict[str, Any]]
    reranked_candidates: list[dict[str, Any]]
    # 合规
    firewall_passed: bool
    firewall_block_reason: str
    # 输出
    ui_schema: dict[str, Any]
    degraded: bool
    degradation_type: str
    latency_ms: int


class AIExecutorDAG:
    """AI 服务执行器 LangGraph DAG

    节点: Entry → CheckFast → FastRetrieval/MonitorHit | RAG+KG(并行) → Normalize → ReRank → Firewall → Fallback/FormatOut → Exit
    """

    def __init__(
        self,
        script_service: ScriptService,
        es_client=None,
        milvus_collection=None,
        embedding_provider=None,
        embedding_breaker=None,
        reranker=None,
        llm_client=None,
        alert_engine=None,
    ) -> None:
        self._script_service = script_service
        self._es_client = es_client
        self._milvus = milvus_collection
        self._embedding = embedding_provider
        self._embedding_breaker = embedding_breaker
        self._reranker = reranker
        self._llm = llm_client
        self._alert_engine = alert_engine
        self._graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """构建 LangGraph DAG"""
        graph = StateGraph(DAGState)

        # 添加节点
        graph.add_node("check_fast", self._check_fast)
        graph.add_node("fast_retrieval", self._fast_retrieval)
        graph.add_node("monitor_hit", self._monitor_hit)
        graph.add_node("rag_chain", self._rag_chain)
        graph.add_node("kg_chain", self._kg_chain)
        graph.add_node("normalize", self._normalize)
        graph.add_node("rerank", self._rerank)
        graph.add_node("firewall", self._firewall)
        graph.add_node("fallback", self._fallback)
        graph.add_node("format_out", self._format_out)

        # 入口
        graph.set_entry_point("check_fast")

        # 条件路由: 快速通路 vs 深度通路
        graph.add_conditional_edges(
            "check_fast",
            self._route_by_confidence,
            {
                "fast": "fast_retrieval",
                "deep": "rag_chain",  # 深度通路先进入 RAG
            },
        )

        # 快速通路
        graph.add_edge("fast_retrieval", "monitor_hit")
        graph.add_edge("monitor_hit", "firewall")

        # 深度通路: RAG 和 KG 并行后汇入 normalize
        graph.add_edge("rag_chain", "normalize")
        graph.add_edge("kg_chain", "normalize")
        graph.add_edge("normalize", "rerank")
        graph.add_edge("rerank", "firewall")

        # 合规防火墙
        graph.add_conditional_edges(
            "firewall",
            self._route_by_firewall,
            {
                "pass": "format_out",
                "block": "fallback",
            },
        )
        graph.add_edge("fallback", "format_out")
        graph.add_edge("format_out", END)

        return graph.compile()

    def _route_by_confidence(self, state: DAGState) -> str:
        """Top1 得分 > 0.9 → 快速通路"""
        if state.get("confidence", 0) > 0.9:
            return "fast"
        return "deep"

    def _route_by_firewall(self, state: DAGState) -> str:
        return "pass" if state.get("firewall_passed", True) else "block"

    # ── DAG 节点实现 ──

    async def _check_fast(self, state: DAGState) -> dict:
        """快速通路判断"""
        return {"path": "fast" if state.get("confidence", 0) > 0.9 else "deep"}

    async def _fast_retrieval(self, state: DAGState) -> dict:
        """标准话术直接命中"""
        intent = IntentLabel(state.get("intent", "faq"))
        scripts = self._script_service.retrieve(intent, top_k=1)
        if scripts:
            return {"fast_script": scripts[0], "fast_path_hit": True}
        return {"fast_script": {}, "fast_path_hit": False}

    async def _monitor_hit(self, state: DAGState) -> dict:
        """快速通路命中记录"""
        logger.info("快速通路命中: session=%s intent=%s", state.get("session_id"), state.get("intent"))
        return {}

    async def _rag_chain(self, state: DAGState) -> dict:
        """RAG 生成链路"""
        if not self._es_client:
            return {"rag_candidates": []}
        try:
            from smartcs.services.common.retrieval import retrieve
            from smartcs.shared.models import RetrieveRequest

            embedding_ok = (
                self._embedding is not None
                and self._embedding_breaker is not None
                and self._embedding_breaker.is_available
            )
            search_type = "hybrid" if embedding_ok else "bm25_only"

            req = RetrieveRequest(query=state.get("message", ""), top_k=5, rerank=False, search_type=search_type)
            resp = await retrieve(
                request=req,
                es_client=self._es_client,
                milvus_collection=self._milvus,
                embedding_provider=self._embedding,
                reranker=self._reranker,
            )
            candidates = [
                {"content": c.content, "score": c.score, "source": c.source_doc, "origin": "rag"}
                for c in resp.results
            ]
            return {"rag_candidates": candidates}
        except Exception as e:
            logger.warning("RAG 链路失败: %s", e)
            return {"rag_candidates": []}

    async def _kg_chain(self, state: DAGState) -> dict:
        """知识图谱推理链路（当前 placeholder，返回空候选）"""
        # TODO: 对接知识图谱服务
        return {"kg_candidates": []}

    async def _normalize(self, state: DAGState) -> dict:
        """候选归一化：RAG Top-5 / KG Top-3"""
        rag = state.get("rag_candidates", [])[:5]
        kg = state.get("kg_candidates", [])[:3]
        all_candidates = rag + kg
        return {"normalized_candidates": all_candidates}

    async def _rerank(self, state: DAGState) -> dict:
        """统一重排序模型

        特征: 语义、情绪、采纳率
        无采纳率数据的候选降权 ×0.7
        """
        candidates = state.get("normalized_candidates", [])
        if not candidates:
            return {"reranked_candidates": []}

        # 如果有 reranker，使用它
        if self._reranker:
            try:
                # 使用 reranker 对候选排序
                texts = [c.get("content", "") for c in candidates]
                query = state.get("message", "")
                results = await self._reranker.rerank(query=query, documents=texts)
                for i, r in enumerate(results):
                    if i < len(candidates):
                        candidates[i]["rerank_score"] = r.relevance_score
                candidates.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
            except Exception as e:
                logger.warning("重排序失败: %s", e)

        # 无采纳率数据的降权
        for c in candidates:
            if "adoption_rate" not in c:
                c["score"] = c.get("score", 0) * 0.7

        return {"reranked_candidates": candidates}

    async def _firewall(self, state: DAGState) -> dict:
        """合规防火墙：短语规则 + 分类器双重过滤 + PII 脱敏"""
        if self._alert_engine:
            try:
                alerts = self._alert_engine.check_compliance(
                    str(state.get("fast_script", "")) + str(state.get("reranked_candidates", ""))
                )
                has_critical = any(a.level == "critical" for a in alerts)
                if has_critical:
                    return {"firewall_passed": False, "firewall_block_reason": "合规拦截"}
            except Exception as e:
                logger.warning("合规检查异常: %s", e)

        return {"firewall_passed": True}

    async def _fallback(self, state: DAGState) -> dict:
        """降级安全兜底话术"""
        from smartcs.services.common.degradation import ContentDegrader
        degrader = ContentDegrader()
        intent = IntentLabel(state.get("intent", "faq"))
        fallback = degrader.get_template(intent)
        return {"degraded": True, "degradation_type": "safe_fallback", "ui_schema": {"fallback": fallback}}

    async def _format_out(self, state: DAGState) -> dict:
        """格式化 UI Schema"""
        if state.get("degraded"):
            # 降级结果，ui_schema 已在 fallback 中设置
            return {}

        # 快速通路结果
        if state.get("path") == "fast" and state.get("fast_script"):
            script = state["fast_script"]
            ui_schema = {
                "scripts": [script] if isinstance(script, dict) else [{"content": str(script)}],
                "knowledge": [],
                "alerts": [],
            }
        elif state.get("reranked_candidates"):
            # 深度通路结果
            top = state["reranked_candidates"][:3]
            ui_schema = {
                "scripts": [],
                "knowledge": [
                    {"chunk_id": c.get("source", ""), "summary": c.get("content", "")[:200], "source": c.get("source", ""), "confidence": "high" if c.get("score", 0) > 0.8 else "medium"}
                    for c in top
                ],
                "alerts": [],
            }
        else:
            ui_schema = {}

        return {"ui_schema": ui_schema}

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        """执行 DAG"""
        t0 = time.monotonic()
        initial_state: DAGState = {
            "session_id": kwargs.get("session_id", ""),
            "message": kwargs.get("message", ""),
            "intent": kwargs.get("intent", "faq"),
            "sentiment": kwargs.get("sentiment", "neutral"),
            "confidence": kwargs.get("confidence", 0.0),
            "state_snapshot": kwargs.get("state_snapshot", {}),
            "trace_id": kwargs.get("trace_id", ""),
            "path": "deep",
            "fast_path_hit": False,
            "fast_script": {},
            "rag_candidates": [],
            "kg_candidates": [],
            "normalized_candidates": [],
            "reranked_candidates": [],
            "firewall_passed": True,
            "firewall_block_reason": "",
            "ui_schema": {},
            "degraded": False,
            "degradation_type": "",
            "latency_ms": 0,
        }
        result = await self._graph.ainvoke(initial_state)
        result["latency_ms"] = int((time.monotonic() - t0) * 1000)
        return result

    @property
    def graph(self):
        return self._graph
```

- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit**

```bash
git add agent/smartcs/services/assist/ai_executor_dag.py agent/tests/test_ai_executor_dag.py
git commit -m "feat: implement E1 AI executor as LangGraph DAG with fast/deep dual path"
```

---

## Task 6: Temporal Activities (D1/D2/D3 + E1/E2/E3)

**Files:**
- Create: `agent/smartcs/workflows/activities.py`
- Test: `agent/tests/test_activities.py`

严格按照文档 §3.3 + §3.4 实现：

**6 个 Activity：**
- `evaluate_d1_service` — 服务评估器：意图置信度 > 阈值，2 轮冷却期
- `evaluate_d2_marketing` — 营销评估器：情绪 + 意图 + Suppress，5 轮冷却期，含动态阈值熔断
- `evaluate_d3_risk` — 风控评估器：始终激活
- `execute_e1_ai_service` — AI 服务执行器：调用 LangGraph DAG，SLA 3s，降级为快速通路/安全兜底
- `execute_e2_marketing` — 营销执行器：纯 Activity 接口（当前无真实 gRPC 服务，返回空结果），SLA 500ms，降级为不展示
- `execute_e3_risk` — 风控执行器：纯 Activity 接口（当前无真实 Java RPC，用本地 AlertEngine），SLA 100ms，降级为放行+待审

**关键设计：**
- 每个 Activity 带独立熔断器（按文档 §3.4 熔断器配置）
- 幂等性：基于 `trace_id` + `executor_id` 去重
- 状态快照注入：从 Redis 读取 versioned 快照

- [ ] **Step 1: Write failing tests**
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement 6 Activities**

```python
# agent/smartcs/workflows/activities.py
"""Temporal Activities

三路评估器 (D1/D2/D3) + 三路执行器 (E1/E2/E3)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from temporalio import activity

from smartcs.services.common.circuit_breaker import CircuitBreaker, CircuitState
from smartcs.shared.config import get_settings
from smartcs.workflows.shared import EvaluatorInput, EvaluatorOutput, ExecutorInput, ExecutorOutput

logger = logging.getLogger(__name__)

# ── 熔断器实例（模块级单例） ──
_ai_breaker: CircuitBreaker | None = None
_mkt_breaker: CircuitBreaker | None = None
_risk_breaker: CircuitBreaker | None = None


def _get_ai_breaker() -> CircuitBreaker:
    global _ai_breaker
    if _ai_breaker is None:
        cfg = get_settings().circuit_breaker
        _ai_breaker = CircuitBreaker(
            name="ai_executor",
            failure_threshold=cfg.ai_failure_rate_threshold,
            slow_call_rate_threshold=cfg.ai_slow_call_rate_threshold,
            slow_call_duration=cfg.ai_slow_call_duration_ms / 1000,
            recovery_timeout=cfg.ai_wait_duration_open_s,
            sliding_window_size=cfg.ai_sliding_window_size,
        )
    return _ai_breaker

# ... 类似创建 _mkt_breaker, _risk_breaker


# ── 评估器 Activities ──

@activity.defn
async def evaluate_d1_service(input: EvaluatorInput) -> EvaluatorOutput:
    """服务评估器 (D1): 意图置信度 > 阈值，2 轮冷却期"""
    settings = get_settings().orchestration
    state = input.state_snapshot

    confidence = state.get("last_confidence", 0.0)
    cooldown = state.get("d1_cooldown_remaining", 0)

    if cooldown > 0:
        return EvaluatorOutput(activated=False, reason=f"冷却中({cooldown}轮)", cooldown_remaining=cooldown)

    activated = confidence > settings.d1_intent_confidence_threshold
    return EvaluatorOutput(
        activated=activated,
        reason=f"置信度={confidence:.2f} 阈值={settings.d1_intent_confidence_threshold}",
        cooldown_remaining=0,
    )


@activity.defn
async def evaluate_d2_marketing(input: EvaluatorInput) -> EvaluatorOutput:
    """营销评估器 (D2): 情绪 + 意图 + Suppress，5 轮冷却期"""
    settings = get_settings().orchestration
    state = input.state_snapshot

    if state.get("suppress_flag", False):
        return EvaluatorOutput(activated=False, reason="营销被压制")

    cooldown = state.get("d2_cooldown_remaining", 0)
    if cooldown > 0:
        return EvaluatorOutput(activated=False, reason=f"冷却中({cooldown}轮)", cooldown_remaining=cooldown)

    emotion = state.get("emotion_vector")
    confidence = state.get("last_confidence", 0.0)

    activated = False
    reason = ""
    if emotion and emotion.get("label") in ("positive", "neutral"):
        score = emotion.get("score", 0.0)
        if score > settings.d2_emotion_score_threshold and confidence > 0.5:
            activated = True
            reason = f"情绪={emotion['label']}({score:.2f}) 置信度={confidence:.2f}"
        else:
            reason = f"情绪/置信度不足: score={score:.2f} conf={confidence:.2f}"
    else:
        reason = "情绪不满足: " + str(emotion)

    return EvaluatorOutput(activated=activated, reason=reason, cooldown_remaining=0)


@activity.defn
async def evaluate_d3_risk(input: EvaluatorInput) -> EvaluatorOutput:
    """风控评估器 (D3): 始终激活"""
    return EvaluatorOutput(activated=True, reason="风控始终激活", cooldown_remaining=0)


# ── 执行器 Activities ──

@activity.defn
async def execute_e1_ai_service(input: ExecutorInput) -> ExecutorOutput:
    """AI 服务执行器 (E1): LangGraph DAG，SLA 3s"""
    breaker = _get_ai_breaker()
    settings = get_settings().orchestration

    if breaker.state == CircuitState.OPEN:
        return ExecutorOutput(
            executor_id="ai_service", degraded=True,
            degradation_type="fast_path_fallback",
            trace_id=input.trace_id,
        )

    t0 = time.monotonic()
    try:
        # 获取 DAG 实例（从 activity context 或全局）
        dag = _get_ai_dag()
        result = await asyncio.wait_for(
            dag.run(
                session_id=input.session_id,
                message=input.message,
                intent=input.intent,
                sentiment=input.sentiment,
                confidence=input.state_snapshot.get("last_confidence", 0.0),
                state_snapshot=input.state_snapshot,
                trace_id=input.trace_id,
            ),
            timeout=settings.e1_sla_ms / 1000,
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        breaker.record_success(elapsed=elapsed / 1000)
        return ExecutorOutput(
            executor_id="ai_service",
            ui_schema=result.get("ui_schema", {}),
            latency_ms=elapsed,
            success=True,
            degraded=result.get("degraded", False),
            degradation_type=result.get("degradation_type", ""),
            trace_id=input.trace_id,
        )
    except asyncio.TimeoutError:
        breaker.record_failure()
        return ExecutorOutput(
            executor_id="ai_service", degraded=True,
            degradation_type="fast_path_fallback",
            latency_ms=int((time.monotonic() - t0) * 1000),
            trace_id=input.trace_id,
        )
    except Exception as e:
        breaker.record_failure()
        logger.warning("E1 执行异常: %s", e)
        return ExecutorOutput(
            executor_id="ai_service", degraded=True,
            degradation_type="safe_fallback",
            trace_id=input.trace_id,
        )


@activity.defn
async def execute_e2_marketing(input: ExecutorInput) -> ExecutorOutput:
    """营销执行器 (E2): 纯 Activity 接口

    当前无真实 gRPC 营销微服务，返回空营销卡片。
    后续对接真实 gRPC 服务只需替换本 Activity 内部实现。
    """
    # TODO: 对接 gRPC 营销微服务
    return ExecutorOutput(
        executor_id="marketing",
        ui_schema={"marketing_cards": []},
        latency_ms=0,
        trace_id=input.trace_id,
    )


@activity.defn
async def execute_e3_risk(input: ExecutorInput) -> ExecutorOutput:
    """风控执行器 (E3): 当前使用本地 AlertEngine

    后续对接 Java RPC 规则引擎只需替换本 Activity 内部实现。
    SLA 100ms，降级策略: 放行 + risk_pending_audit: true
    """
    breaker = _get_risk_breaker()
    settings = get_settings().orchestration

    if breaker.state == CircuitState.OPEN:
        return ExecutorOutput(
            executor_id="risk", degraded=True,
            degradation_type="pass_with_audit_flag",
            risk_action="PASS",
            ui_schema={"action": "PASS", "risk_pending_audit": True},
            trace_id=input.trace_id,
        )

    t0 = time.monotonic()
    try:
        alert_engine = _get_alert_engine()
        alerts = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, alert_engine.check_compliance, input.message
            ),
            timeout=settings.e3_sla_ms / 1000,
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        breaker.record_success(elapsed=elapsed / 1000)

        from smartcs.shared.models import AlertLevel
        has_critical = any(a.level == AlertLevel.CRITICAL for a in alerts)
        has_warning = any(a.level == AlertLevel.WARNING for a in alerts)

        if has_critical:
            action = "BLOCK"
            ui = {"action": "BLOCK", "reason": "合规风险", "alerts": [{"level": a.level.value, "message": a.message} for a in alerts]}
        elif has_warning:
            action = "WARN"
            ui = {"action": "WARN", "alerts": [{"level": a.level.value, "message": a.message} for a in alerts]}
        else:
            action = "PASS"
            ui = {"action": "PASS"}

        return ExecutorOutput(
            executor_id="risk", ui_schema=ui,
            latency_ms=elapsed, risk_action=action,
            trace_id=input.trace_id,
        )
    except asyncio.TimeoutError:
        breaker.record_failure()
        return ExecutorOutput(
            executor_id="risk", degraded=True,
            degradation_type="pass_with_audit_flag",
            risk_action="PASS",
            ui_schema={"action": "PASS", "risk_pending_audit": True},
            trace_id=input.trace_id,
        )
    except Exception as e:
        breaker.record_failure()
        return ExecutorOutput(
            executor_id="risk", degraded=True,
            degradation_type="pass_with_audit_flag",
            risk_action="PASS",
            trace_id=input.trace_id,
        )
```

- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit**

```bash
git add agent/smartcs/workflows/activities.py agent/tests/test_activities.py
git commit -m "feat: implement 6 Temporal Activities (D1/D2/D3 evaluators + E1/E2/E3 executors)"
```

---

## Task 7: Temporal Orchestration Workflow

**Files:**
- Create: `agent/smartcs/workflows/orchestration_workflow.py`
- Test: `agent/tests/test_workflow.py`

严格按照文档 §3.3 OE 状态机 + 编排策略矩阵实现：

**OE 状态机:** IDLE → EVALUATING → DISPATCHING → WAITING_RESULTS → COMPLETED

**编排策略矩阵:**
1. `service_suppresses_marketing`: D1.activated → D2.force_suppress=true, duration 2轮
2. `service_with_risk_parallel`: D1 AND D3 → [E1, E3].parallel_execute(), timeout=min(E1.SLA, E3.SLA)
3. `risk_block_skip_marketing`: E3.result.action=="BLOCK" → D2.force_skip=true
4. `marketing_deferred`: D1 AND D2 → E2.execute_after(E1.complete, delay=500ms)

**全局超时 5s 强制推进**

- [ ] **Step 1: Write failing tests**
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement OrchestrationWorkflow**

```python
# agent/smartcs/workflows/orchestration_workflow.py
"""编排引擎 Temporal Workflow

OE 状态机: IDLE → EVALUATING → DISPATCHING → WAITING_RESULTS → COMPLETED
严格按照设计文档 §3.3。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from smartcs.workflows.shared import (
    EvaluatorInput,
    EvaluatorOutput,
    ExecutorInput,
    ExecutorOutput,
    OrchestrationResult,
)
from smartcs.workflows.activities import (
    evaluate_d1_service,
    evaluate_d2_marketing,
    evaluate_d3_risk,
    execute_e1_ai_service,
    execute_e2_marketing,
    execute_e3_risk,
)

logger = logging.getLogger(__name__)

# Activity 重试策略：不重试（执行器自行处理重试/降级）
_NO_RETRY = RetryPolicy(maximum_attempts=1)


@workflow.defn
class OrchestrationWorkflow:
    """首席会话编排器 Workflow

    每次客户消息触发一次完整的 OE 周期。
    """

    @workflow.run
    async def run(self, input: ExecutorInput) -> OrchestrationResult:
        """执行完整的 OE 周期"""
        t0 = workflow.now()
        settings = get_settings().orchestration  # 注意: workflow 中不能直接调用，需通过 input 传入

        # ── IDLE → EVALUATING ──
        # 并行执行 D1/D2/D3 评估
        d1_result, d2_result, d3_result = await asyncio.gather(
            workflow.execute_activity(
                evaluate_d1_service,
                EvaluatorInput(session_id=input.session_id, state_snapshot=input.state_snapshot),
                start_to_close_timeout=timedelta(seconds=1),
                retry_policy=_NO_RETRY,
            ),
            workflow.execute_activity(
                evaluate_d2_marketing,
                EvaluatorInput(session_id=input.session_id, state_snapshot=input.state_snapshot),
                start_to_close_timeout=timedelta(seconds=1),
                retry_policy=_NO_RETRY,
            ),
            workflow.execute_activity(
                evaluate_d3_risk,
                EvaluatorInput(session_id=input.session_id, state_snapshot=input.state_snapshot),
                start_to_close_timeout=timedelta(seconds=1),
                retry_policy=_NO_RETRY,
            ),
        )

        # ── EVALUATING → DISPATCHING ──
        # 应用编排策略矩阵
        plan = self._apply_policies(d1_result, d2_result, d3_result, input.state_snapshot)

        # ── DISPATCHING → WAITING_RESULTS ──
        # 按执行计划调度执行器
        results: dict[str, ExecutorOutput] = {}

        # 策略: service_with_risk_parallel — E1 和 E3 并行
        parallel_tasks = []
        if d1_result.activated:
            parallel_tasks.append(("ai_service", execute_e1_ai_service, timedelta(seconds=3)))
        if d3_result.activated:
            parallel_tasks.append(("risk", execute_e3_risk, timedelta(seconds=3)))

        if parallel_tasks:
            exec_results = await asyncio.gather(*[
                workflow.execute_activity(
                    activity_fn,
                    input,
                    start_to_close_timeout=timeout,
                    retry_policy=_NO_RETRY,
                )
                for _, activity_fn, timeout in parallel_tasks
            ])
            for (name, _, _), result in zip(parallel_tasks, exec_results):
                results[name] = result

        # 策略: risk_block_skip_marketing — 风控拦截后跳过营销
        risk_output = results.get("risk")
        if risk_output and risk_output.risk_action == "BLOCK":
            pass  # 跳过营销
        elif d2_result.activated and not plan.get("d2_suppressed"):
            # 策略: marketing_deferred — 营销在服务后追加 (delay 500ms)
            if d1_result.activated:
                await asyncio.sleep(0.5)
            mkt_result = await workflow.execute_activity(
                execute_e2_marketing,
                input,
                start_to_close_timeout=timedelta(milliseconds=500),
                retry_policy=_NO_RETRY,
            )
            results["marketing"] = mkt_result

        # ── WAITING_RESULTS → COMPLETED ──
        # 仲裁融合
        arbitration = self._arbitrate(results)

        elapsed = (workflow.now() - t0).total_seconds() * 1000
        arbitration.elapsed_ms = int(elapsed)
        arbitration.trace_id = input.trace_id
        return arbitration

    def _apply_policies(
        self,
        d1: EvaluatorOutput,
        d2: EvaluatorOutput,
        d3: EvaluatorOutput,
        state: dict,
    ) -> dict:
        """应用编排策略矩阵"""
        # 策略: service_suppresses_marketing
        d2_suppressed = d1.activated and d2.activated
        return {"d2_suppressed": d2_suppressed}

    def _arbitrate(self, results: dict[str, ExecutorOutput]) -> OrchestrationResult:
        """优先级融合展示规则（文档 §3.5）"""
        risk = results.get("risk")
        ai = results.get("ai_service")
        mkt = results.get("marketing")

        risk_action = risk.risk_action if risk else "PASS"

        if risk_action == "BLOCK":
            # BLOCK: 主卡片=风控拦截, 辅助=服务(只读), 营销=不展示
            return OrchestrationResult(
                primary_card={"type": "risk_block", "content": risk.ui_schema if risk else {}},
                risk_badge=None,
                marketing_slot=None,
                fusion_type="risk_blocked",
            )
        elif risk_action == "WARN":
            # WARN: 主卡片=服务, 风险标记=徽章, 营销=降级小卡片
            return OrchestrationResult(
                primary_card={"type": "service_answer", "content": ai.ui_schema if ai else {}},
                risk_badge={"type": "risk_badge", "alerts": risk.ui_schema.get("alerts", []) if risk else []},
                marketing_slot={"type": "marketing_small", "content": mkt.ui_schema if mkt else {}} if mkt else None,
                fusion_type="service_risk_warn",
            )
        else:
            # PASS: 主卡片=服务, 辅助=营销(标准)
            return OrchestrationResult(
                primary_card={"type": "service_answer", "content": ai.ui_schema if ai else {}},
                risk_badge=None,
                marketing_slot={"type": "marketing_standard", "content": mkt.ui_schema if mkt else {}} if mkt else None,
                fusion_type="service_marketing" if mkt else "service_only",
            )
```

- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit**

```bash
git add agent/smartcs/workflows/orchestration_workflow.py agent/tests/test_workflow.py
git commit -m "feat: implement Temporal OrchestrationWorkflow with OE state machine and policy matrix"
```

---

## Task 8: Global Arbitrator (PII 脱敏 + 合规短语过滤)

**Files:**
- Create: `agent/smartcs/services/assist/arbitrator.py`
- Test: `agent/tests/test_arbitrator.py`

严格按照文档 §3.5 实现：
- 优先级融合规则（已在 Task 7 Workflow 中简化实现，这里实现完整版）
- 全局合规校验：PII 脱敏 + 合规短语过滤
- Schema 卡片渲染引擎

- [ ] **Step 1: Write failing tests**
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement GlobalArbitrator**

PII 脱敏规则（文档 §4.2）：
```python
_PII_PATTERNS = [
    (re.compile(r"[\u4e00-\u9fa5]{2,4}"), "[NAME]"),       # 姓名
    (re.compile(r"1[3-9]\d{9}"), "[PHONE]"),                # 手机
    (re.compile(r"\d{17}[\dXx]"), "[IDCARD]"),              # 身份证
    (re.compile(r"\d{16,19}"), "[BANKCARD]"),               # 银行卡
]
```

- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit**

```bash
git add agent/smartcs/services/assist/arbitrator.py agent/tests/test_arbitrator.py
git commit -m "feat: add global arbitrator with priority fusion rules and PII masking"
```

---

## Task 9: DI Wiring + Main.py Integration

**Files:**
- Modify: `agent/smartcs/services/common/deps.py`
- Modify: `agent/smartcs/main.py`

更新 assist 服务启动步骤：
1. init_db
2. init_redis
3. init_elasticsearch
4. init_milvus
5. init_minio
6. init_embedding
7. init_reranker
8. init_grpc_channels
9. init_llm
10. init_session_manager
11. init_classifier
12. init_assist_orchestrator (保留，作为 DAG 的 script_service 依赖)
13. **init_state_manager** (新增)
14. **init_temporal_client** (新增)
15. **init_temporal_worker** (新增)
16. _init_assist_ws_pool

- [ ] **Step 1: Add init/close/get functions for StateManager, TemporalClient, Worker**
- [ ] **Step 2: Update main.py assist lifespan**
- [ ] **Step 3: Run tests**
- [ ] **Step 4: Commit**

```bash
git add agent/smartcs/services/common/deps.py agent/smartcs/main.py
git commit -m "feat: wire Temporal client, worker, and state manager into assist service"
```

---

## Task 10: Router Integration — Temporal Workflow Trigger

**Files:**
- Modify: `agent/smartcs/services/assist/router.py`

替换 /analyze 端点为触发 Temporal Workflow：
1. 意图分类
2. 启动 OrchestrationWorkflow
3. 等待结果（全局 5s 超时）
4. 通过 WebSocket 推送

新增 POST /api/feedback 端点（隐式反馈收集）。

文档 §3.6 反馈：
```
操作类型映射:
  直接发送 → {feedback: "accept", confidence: 1.0}
  修改后发送 → {feedback: "modify", confidence: 0.5, modify_fields: [...]}
  复制部分内容 → {feedback: "partial_accept", confidence: 0.3}
  忽略 → {feedback: "reject", confidence: 0.0}
延迟确认: 座席操作后 3s 内可撤销, 3s 后确认写入
```

- [ ] **Step 1: Update /analyze to trigger Temporal Workflow**
- [ ] **Step 2: Add /feedback endpoint**
- [ ] **Step 3: Run tests**
- [ ] **Step 4: Commit**

```bash
git add agent/smartcs/services/assist/router.py
git commit -m "feat: integrate Temporal Workflow into analyze endpoint, add feedback endpoint"
```

---

## Task 11: Full Test Suite & Regressions

- [ ] **Step 1: Run full test suite**
- [ ] **Step 2: Fix regressions**
- [ ] **Step 3: Run lint and type check**
- [ ] **Step 4: Commit fixes**

---

## Self-Review

### Spec Coverage

| 文档章节 | 覆盖 Task |
|---------|----------|
| §2 AI 服务执行器 LangGraph DAG | Task 5 |
| §3.1 输入与感知层 | Plan B（后续） |
| §3.2 统一状态层 CAS + 覆写规则 | Task 3 |
| §3.3 宏观编排层 Temporal Workflow + D1/D2/D3 + 策略矩阵 | Task 6 + Task 7 |
| §3.4 微观执行层 E1/E2/E3 + 熔断器 + 幂等性 | Task 6 |
| §3.5 仲裁与输出层 优先级融合 + 合规校验 | Task 7 + Task 8 |
| §3.6 反馈闭环层 实时信号 | Task 10 |
| §4.1 可观测性 trace_id | Task 5/6/7 (trace_id 贯穿) |
| §4.2 安全与隐私 PII 脱敏 | Task 8 |
| §5 降级矩阵 | Task 1 (熔断器) + Task 6 (各执行器降级) |
