# Sprint 3 设计文档：Agent 编排 + Bot 对话 MVP

> 日期：2026-05-01
> 状态：已批准
> 范围：意图分类、会话状态管理、Agent 编排图、转人工机制

## 1. 概述

Sprint 3 在 Sprint 1（基础设施骨架）和 Sprint 2（RAG 核心 + 知识库）之上，构建完整的对话能力：从用户输入到最终回复的端到端链路。

### 核心架构决策

| 决策项 | 选择 | 原因 |
|--------|------|------|
| Agent 编排 | LangGraph StateGraph + Supervisor | 确定性路由，状态自动持久化，客服场景不允许 LLM 自主选路 |
| 意图分类 | 双通道 Fast/Slow | 规则覆盖高频（<50ms），LLM 兜底模糊/长尾（~1-3s） |
| Slow Path 模型 | Qwen2.5-7B (Ollama) | 项目已有 Ollama 配置，7B 三合一（意图+实体+情感），无需标注数据管线 |
| 会话状态 | 通过 LangGraph state 读写 | 单一状态源，避免与 Checkpointer 双写冲突 |
| 转人工 | L1 关键词 > L2 语义 > L3 累计 | 渐进式安全网，确保用户不会卡在 bot 循环 |

### 实施路径

自底向上，每层可独立测试：

```
第1层：会话状态管理（session.py + llm.py）
第2层：意图分类 + 转人工判断（classifier.py + transfer.py）
第3层：Agent 编排图（agent.py + prompts.py）
第4层：端点对接（router.py + deps.py）
```

## 2. 会话状态管理

### 架构

```
┌─────────────────────────────────────────────────┐
│  SessionManager（业务逻辑层）                      │
│  · create_session / get_session / add_turn       │
│  · check_transfer_condition / transition_phase   │
│  · 不直接与 LangGraph Checkpointer 竞争           │
└──────────────────┬──────────────────────────────┘
                   │ 读写
┌──────────────────▼──────────────────────────────┐
│  Redis                                           │
│  · smartcs:session:{id}:meta    (会话元信息)       │
│  · smartcs:session:{id}:history (对话历史 List)    │
└─────────────────────────────────────────────────┘
```

### 关键设计修正

| 原始设计问题 | 修正 | 原因 |
|-------------|------|------|
| SessionManager 直接操作 Redis 与 LangGraph Checkpointer 双写 | SessionManager 管理业务元信息，不与 Checkpointer 竞争 | 避免双 source of truth |
| 乐观锁 (WATCH/MULTI) | 去掉 | 单用户会话无并发，aioredis 支持差 |
| 单一 JSON Key 存全量状态 | 拆分 meta + history | 避免长对话全量序列化 |
| 独立 intent_history 字段 | 从 DialogueTurn history 提取 | 去冗余 |
| metadata: Dict[str, Any] | Pydantic model 约束 | 防止 schema 漂移 |

### 新增模块

- `src/smartcs/services/common/session.py` — SessionManager

## 3. 双通道意图分类

### 架构

```
用户输入
  │
  ├── Fast Path（RuleClassifier）───── confidence ≥ 0.7 ──→ 直接使用
  │    · 正则匹配 + 关键词匹配                │
  │    · 覆盖 70-80% 高频意图                 │
  │                                         │
  └── confidence < 0.7 ──→ Slow Path（LLMClassifier）
                            · Qwen2.5-7B (Ollama)
                            · json_mode 结构化输出
                            · Few-shot 示例
                            · 超时 3s + 熔断
                            │
                            └── 失败兜底
                                  · intent = FAQ
                                  · confidence = 0.0
```

### 意图域路由

| 域 | 路由到 | 意图 |
|----|--------|------|
| knowledge | knowledge_agent | bill_query, transaction_query, limit_query, installment_inquiry, reward_query, faq |
| business | business_agent | card_loss, complaint, transfer_agent |
| fallback | fallback_agent | chitchat, unknown |

### 新增模块

- `src/smartcs/services/common/classifier.py` — RuleClassifier, LLMClassifier, IntentClassifier
- `src/smartcs/services/common/llm.py` — LLMClient, LLMCircuitBreaker

## 4. LangGraph Agent 编排图

### 图结构

```
                    ┌──────────┐
                    │  START   │
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │ classify │  ← 双通道分类节点
                    │  intent  │
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │ supervisor│  ← 路由决策
                    └────┬─────┘
                         │
          ┌──────────────┼──────────────┐
          │              │              │
    ┌─────▼─────┐ ┌─────▼──────┐ ┌────▼─────┐
    │ knowledge │ │ business   │ │ fallback │
    │   agent   │ │   agent    │ │  agent   │
    │(RAG检索+  │ │(挂失/投诉  │ │(闲聊/兜底│
    │ LLM生成)  │ │ →转人工)   │ │  回复)   │
    └─────┬─────┘ └─────┬──────┘ └────┬─────┘
          │              │              │
          └──────────────┼──────────────┘
                         │
                    ┌────▼──────┐
                    │  transfer │  ← 转人工判断
                    │   check   │
                    └────┬──────┘
                         │
              ┌──────────┼──────────┐
              │                     │
        ┌─────▼─────┐        ┌─────▼─────┐
        │  respond  │        │  handoff  │
        │  (正常回复)│        │ (转人工)   │
        └─────┬─────┘        └─────┬─────┘
              │                     │
              └──────────┬──────────┘
                         │
                    ┌────▼─────┐
                    │   END    │
                    └──────────┘
```

### AgentState 定义

```python
class AgentState(dict):
    session_id: str
    user_input: str
    intent: IntentResult | None
    entities: list[Entity]
    sentiment: SentimentLabel
    classify_source: str          # "rule" | "llm" | "fallback"
    domain: str                   # "knowledge" | "business" | "fallback"
    retrieval_context: str
    response: str
    should_transfer: bool
    transfer_reason: str
    session_state: SessionState | None
```

### 各 Agent 职责

| Agent | 触发条件 | 核心逻辑 |
|-------|---------|---------|
| knowledge_agent | 意图 ∈ 账单/额度/积分/年费/FAQ 域 | RAG 检索 → LLM 生成；检索为空时返回"未找到" |
| business_agent | 意图 ∈ 挂失/投诉/转人工域 | Sprint 3 简化：直接触发转人工 |
| fallback_agent | 闲聊/兜底域 | 问候/告别快速匹配 → LLM 闲聊 → 模板兜底 |

### 降级链

```
LLM 生成 → 检索摘要 → 预设模板回复 → "请稍后重试"
```

### 新增模块

- `src/smartcs/services/bot/agent.py` — SmartCSAgent, 节点函数
- `src/smartcs/services/bot/prompts.py` — Prompt 模板

## 5. 转人工机制

### 三级触发

| 级别 | 触发条件 | 举例 |
|------|---------|------|
| L1 关键词 | 用户输入命中转人工词/敏感词 | "人工"、"投诉"、"炸弹" |
| L2 语义 | 情感负面+高置信度 / 投诉意图 / transfer_agent 意图 | "你们服务太差了" |
| L3 累计 | 连续 3 轮低置信度 / 最近 5 轮中 3 轮兜底 | 反复无法理解 |

**优先级**：L1 > L2 > L3，命中高级别直接触发，不继续检查。

### 新增模块

- `src/smartcs/services/common/transfer.py` — TransferChecker
- `config/transfer_keywords.txt` — 转人工关键词表

## 6. /api/chat 端点改造

```python
@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, agent: AgentDep, session_manager: SessionManagerDep):
    # 1. 获取/创建会话
    session = await session_manager.get_or_create(request.session_id, ...)
    # 2. 记录用户消息
    await session_manager.add_turn(session.session_id, user_turn)
    # 3. 运行 Agent 图
    result = await agent.run(session.session_id, request.message)
    # 4. 返回响应
    return ChatResponse(session_id=..., reply=..., intent=..., is_transfer=...)
```

## 7. 新增文件清单

```
src/smartcs/services/common/
  ├── llm.py              # LLM 调用封装（结构化输出 + 熔断 + 降级）
  ├── classifier.py       # 双通道分类器（Rule + LLM）
  ├── session.py          # 会话状态管理
  └── transfer.py         # 转人工判断逻辑

src/smartcs/services/bot/
  ├── agent.py            # LangGraph 状态图定义 + 节点函数
  └── prompts.py          # LLM Prompt 模板

config/
  └── transfer_keywords.txt   # 转人工关键词表

tests/
  ├── test_llm.py         # 9 测试：熔断器 + chat + chat_json + classify
  ├── test_classifier.py  # 15 测试：规则匹配 + LLM + 双通道 + 域路由
  ├── test_session.py     # 10 测试：创建/加载/追加/切换/删除
  ├── test_transfer.py    # 11 测试：L1/L2/L3 触发 + 优先级 + 无触发
  └── test_agent.py       # 9 测试：初始状态 + 路由 + 条件边
```

## 8. 修改文件清单

| 文件 | 修改内容 |
|------|---------|
| `src/smartcs/services/common/deps.py` | 新增 LLM/Session/Classifier/Transfer/Agent 的 init/close/DI 函数 |
| `src/smartcs/services/bot/router.py` | /api/chat stub 替换为真实 Agent 调用 |
| `src/smartcs/main.py` | bot_lifespan 增加 LLM/Session/Classifier/Transfer/Agent 初始化 |
| `tests/test_bot_api.py` | chat 测试适配新的 Agent 依赖 |

## 9. 测试结果

```
127 passed, 1 skipped in 9.72s
```

- 55 个 Sprint 3 新增测试全部通过
- 72 个既有测试全部通过
- 1 个跳过（chat 集成测试需完整中间件环境）
