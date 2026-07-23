# 我用 FastAPI + LangGraph 写了一个银行级智能客服，全链路私有化开源了

> 一句话：**SmartCS（青雀）** 是一个可私有化部署的银行信用卡智能客服参考实现 —— Bot 自助问答 + AI 坐席辅助双引擎，RAG 检索增强、意图识别、合规过滤、熔断降级、实时监控全配齐，`make demo` 一条命令即可体验。
>
> 仓库：https://github.com/slowleelab/QingQue ｜ License：Apache 2.0

![demo](https://raw.githubusercontent.com/slowleelab/QingQue/main/docs/assets/demo.gif)

## 为什么做这个

市面上智能客服方案不少，但要么是绑死公有云的 SaaS，要么是只有 Demo 级 RAG 的玩具项目。金融场景有几个硬约束，是大多数开源项目覆盖不到的：

- **数据不出域**：客户对话、卡片信息必须私有化，LLM 也得能跑在本地（Ollama / vLLM）
- **合规可审计**：每句话要过敏感词/合规过滤，对话留痕满足银行 5-7 年审计要求
- **高可用不能靠运气**：LLM 挂了、向量库挂了，服务不能跟着挂 —— 要有完整的熔断 + 多级降级链
- **人机协同**：机器人搞不定的要平滑转人工，坐席通话中还要实时给话术建议

没有现成方案同时满足这几点，于是我把整套系统搭了出来并开源。

## 三层架构

```
┌─────────────────────────────────────────────┐
│  编排层 (FastAPI)                             │
│  ┌──────────────┐   ┌──────────────────────┐ │
│  │ Bot 自助服务  │   │  AI 坐席辅助服务      │ │
│  │   :8000      │   │  :8001 (WebSocket)   │ │
│  └──────┬───────┘   └──────────┬───────────┘ │
├─────────┼──────────────────────┼──────────────┤
│  AI 能力层 (gRPC / 可插拔)      │              │
│  意图分类 │ RAG 检索 │ LLM │ 安全过滤          │
├───────────────────────────────────────────────┤
│  数据层                                        │
│  PostgreSQL · Redis · ES(IK) · Milvus · MinIO · Kafka · Temporal │
└───────────────────────────────────────────────┘
```

设计上有几个我认为值得分享的点：

### 1. 检索不是"向量一梭子"，而是混合检索 + RRF 融合

金融术语（"年费减免""容时容差""MCC 码"）纯向量检索召回不稳。做法是 **BM25（ES + IK 分词）+ 向量（Milvus）双路召回，RRF 融合排序，再过重排模型**。任何一路挂了就优雅降级成单路，不报错。

### 2. 熔断降级是"一条链"，不是一个开关

LLM 是金融私有化场景里最不稳定的依赖（本地小模型、显存抖动、推理超时）。降级链是：

```
LLM 生成 → 检索摘要拼接 → 预置话术模板 → 兜底文案
```

每一级都有 `CircuitBreaker`（失败率阈值 + 滑动窗口 + 恢复超时），配合健康监控。结果是：**哪怕 Ollama 整个宕机，Bot 依然能用模板给出合格回答**，只是"不够聪明"而非"直接报错"。这也是 `make demo` 敢宣称"无本地大模型也能跑"的底气。

### 3. 对话状态机 + Redis 反馈缓冲

会话不是无状态问答，而是 `bot → handoff → assist → ended` 的完整生命周期。坐席对 AI 建议的"采纳/修改"反馈，用 **Redis 缓冲 + 3s 延迟提交**合并高频操作，避免每敲一个字都写一次库。

### 4. 合规过滤前置 + 全量对话留痕

敏感词库支持热更新（改库即时生效，不用重启），用户输入先过安全过滤再进 Agent；所有对话落 `dialogue_log` 表，满足银行审计留存要求。

## 5 分钟跑起来

最大的心智负担是中间件一堆。所以做了一键 Demo：在基础编排上叠一个 `docker-compose.demo.yml`，多跑一个一次性的 `demo-init` 容器（自动 `alembic upgrade head` + 灌入预置知识库，幂等可重复），然后 Bot/Assist 以容器内主机名拉起。

```bash
git clone https://github.com/slowleelab/QingQue.git && cd QingQue
make demo
```

起来后直接问一句：

```bash
curl -X POST http://localhost:8000/api/chat/send \
  -H 'Content-Type: application/json' \
  -d '{"message":"信用卡年费怎么减免"}'
```

会得到一个带意图识别结果的回答：`{"intent":"limit_query","confidence":0.75,"source":"llm", ...}`。有 Ollama 走真实大模型；没有就自动降级，流程照样通。

## 一些数字

- **39 个测试文件 / 474 条用例**，覆盖 RAG、熔断、会话状态机、降级链等核心路径
- 12 个 Pydantic-settings 子配置类，全部走环境变量，密钥不落文件
- 统一的错误码体系（2xxx 输入 / 3xxx 业务 / 4xxx 外部 / 5xxx 系统），全局中间件映射成一致的错误 JSON

## 适合谁

- 金融/政企想做**私有化智能客服**，需要一个能落地的技术基座
- 想学习 **RAG + Agent 编排 + 熔断降级**在真实业务里怎么组合，而不是玩具 Demo
- 团队要快速验证智能客服 PoC，不想从零搭中间件和降级策略

## 接下来

- [ ] 坐席辅助的并行 坐席辅助引擎（Temporal + LangGraph DAG 升级）
- [ ] 更完整的知识库运营后台
- [ ] 更多 LLM 后端适配（vLLM / OpenAI 兼容）

如果这个项目对你有帮助，欢迎 [Star](https://github.com/slowleelab/QingQue) ⭐；有问题去 [Discussions](https://github.com/slowleelab/QingQue/discussions) 聊，报 Bug 走 [Issues](https://github.com/slowleelab/QingQue/issues)。也欢迎 PR —— 先从[贡献指南](https://github.com/slowleelab/QingQue/blob/main/CONTRIBUTING.md)开始。
