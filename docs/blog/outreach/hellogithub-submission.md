# HelloGitHub 自荐文案

> 到 https://github.com/521xueweihan/HelloGitHub 提 issue，选"推荐项目"模板，按下面对应字段粘贴。
>
> 项目展示名：**灵智（Lumio）**，仓库地址 `https://github.com/slowleelab/lumio` 不变。

---

**项目地址**：https://github.com/slowleelab/lumio

**类别**：Python / 人工智能 / 企业应用

**项目标题**：灵智（Lumio）— 银行级私有化智能客服参考实现

**项目描述（100 字内）**：

> 可私有化部署的银行信用卡智能客服：RAG 检索增强机器人自助问答 + 通话中实时 AI 坐席辅助。内置混合检索（BM25+向量+RRF）、意图识别、合规过滤、熔断降级链、22 个 Java MCP 信用卡工具与全链路监控，数据不出域、本地大模型可跑。FastAPI + asyncio，`make demo` 一条命令即可体验完整系统。

**亮点（让人眼前一亮的点）**：

- 🤖 **双引擎**：Bot 自助问答 + AI 坐席辅助（WebSocket 实时推话术/知识/合规提醒）
- 🔍 **混合检索**：ES(IK) BM25 + Milvus 向量双路召回，RRF 融合 + 重排，单路故障自动降级
- 🛡️ **金融级可靠**：LLM→检索摘要→模板→兜底 四级熔断降级链，Ollama 宕机服务也不挂
- 🔌 **MCP 工具层**：Java Spring AI MCP Server 暴露 22 个信用卡工具（账单/卡服务/额度/分期/还款/积分/交易，全部 mock），Higress + Nacos 统一治理
- 📜 **合规可审计**：敏感词热更新、全量对话留痕，满足银行 5-7 年审计要求
- 🐳 **一键体验**：`make demo` 拉起中间件 + 迁移 + 预置知识库 + 双服务，无需本地 Python 环境

**截图 / 演示**：

![demo](https://raw.githubusercontent.com/slowleelab/lumio/main/docs/assets/demo.gif)

**示例代码（可选）**：

```bash
git clone https://github.com/slowleelab/lumio.git && cd lumio
make demo   # 中间件 + 迁移 + 知识库 + Bot:8000 + Assist:8001

# 问一句
curl -X POST http://localhost:8000/api/chat/send \
  -H 'Content-Type: application/json' \
  -d '{"message":"信用卡年费怎么减免"}'
```

**后续更新计划**：

- 知识平台独立服务化（取代 Milvus 双写，统一用 ES dense_vector + RRF）
- 适配更多 LLM 后端（vLLM / OpenAI 兼容接口）
- Temporal 替换为内置 asyncio 调度

**推荐理由**：

> 市面上要么是绑死公有云的客服 SaaS，要么是玩具级 RAG Demo。灵智（Lumio）是少见的、把"私有化 + 合规 + 高可用降级 + MCP 工具"真正做完整并开源的银行级参考实现，6 个 Sprint 728 条测试用例、22 个 Java MCP 工具，适合金融/政企落地和学习 RAG + Agent 工程化。

---

## 备用：一段话速推版（社群/朋友圈/即刻）

> 开源了一个银行级私有化智能客服【灵智（Lumio）】：RAG 机器人问答 + 实时 AI 坐席辅助 + 22 个 MCP 信用卡工具，混合检索(BM25+向量+RRF)、意图识别、合规过滤、四级熔断降级、全链路监控全配齐，数据不出域、本地大模型可跑。FastAPI + asyncio + Spring AI，`make demo` 一条命令体验。Apache 2.0，欢迎 Star ⭐ https://github.com/slowleelab/lumio
