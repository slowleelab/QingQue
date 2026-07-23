# HelloGitHub 自荐文案

> 到 https://github.com/521xueweihan/HelloGitHub 提 issue，选"推荐项目"模板，按下面对应字段粘贴。

---

**项目地址**：https://github.com/slowleelab/QingQue

**类别**：Python / 人工智能 / 企业应用

**项目标题**：SmartCS（青雀）— 银行级私有化智能客服参考实现

**项目描述（100 字内）**：

> 可私有化部署的银行信用卡智能客服：RAG 检索增强机器人自助问答 + 通话中实时 AI 坐席辅助。内置混合检索（BM25+向量+RRF）、意图识别、合规过滤、熔断降级链与全链路监控，数据不出域、本地大模型可跑。FastAPI + LangGraph，`make demo` 一条命令即可体验完整系统。

**亮点（让人眼前一亮的点）**：

- 🤖 **双引擎**：Bot 自助问答 + AI 坐席辅助（WebSocket 实时推话术/知识/合规提醒）
- 🔍 **混合检索**：ES(IK) BM25 + Milvus 向量双路召回，RRF 融合 + 重排，单路故障自动降级
- 🛡️ **金融级可靠**：LLM→检索摘要→模板→兜底 四级熔断降级链，Ollama 宕机服务也不挂
- 📜 **合规可审计**：敏感词热更新、全量对话留痕，满足银行 5-7 年审计要求
- 🐳 **一键体验**：`make demo` 拉起中间件 + 迁移 + 预置知识库 + 双服务，无需本地 Python 环境

**截图 / 演示**：

![demo](https://raw.githubusercontent.com/slowleelab/QingQue/main/docs/assets/demo.gif)

**示例代码（可选）**：

```bash
git clone https://github.com/slowleelab/QingQue.git && cd QingQue
make demo   # 中间件 + 迁移 + 知识库 + Bot:8000 + Assist:8001

# 问一句
curl -X POST http://localhost:8000/api/chat/send \
  -H 'Content-Type: application/json' \
  -d '{"message":"信用卡年费怎么减免"}'
```

**后续更新计划**：

- 坐席辅助并行 坐席辅助引擎（Temporal + LangGraph DAG 架构升级）
- 知识库运营后台完善
- 适配更多 LLM 后端（vLLM / OpenAI 兼容接口）

**推荐理由**：

> 市面上要么是绑死公有云的客服 SaaS，要么是玩具级 RAG Demo。SmartCS 是少见的、把"私有化 + 合规 + 高可用降级"真正做完整并开源的银行级参考实现，474 条测试用例，适合金融/政企落地和学习 RAG+Agent 工程化。

---

## 备用：一段话速推版（社群/朋友圈/即刻）

> 开源了一个银行级私有化智能客服【SmartCS·青雀】：RAG 机器人问答 + 实时 AI 坐席辅助，混合检索(BM25+向量+RRF)、意图识别、合规过滤、四级熔断降级、全链路监控全配齐，数据不出域、本地大模型可跑。FastAPI + LangGraph，`make demo` 一条命令体验。Apache 2.0，欢迎 Star ⭐ https://github.com/slowleelab/QingQue
