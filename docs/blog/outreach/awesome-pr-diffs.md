# Awesome-List 投稿 PR（可直接套用）

> 网络受限未能拉取各列表实时 README，以下 diff 基于 sindresorhus/awesome 通用规范编写。
> **提交前请打开目标列表的 CONTRIBUTING.md 核对 3 点**：① 描述结尾是否带句号；② 是否要求按字母序插入；③ 插入到哪个小节。多数列表差异只在这三处。

> 仓库名 `slowleelab/lumio` 不变（青雀是社区/组织名），项目展示名为 **灵智（Lumio）**。

---

## 通用条目（中英两版，选一）

英文（投国际列表）：
```markdown
- [Lumio](https://github.com/slowleelab/lumio) - Self-hostable banking-grade intelligent customer service with a RAG chatbot, real-time agent assist, and 22 MCP credit-card tools, featuring compliance filtering and circuit-breaker degradation chains. Built with FastAPI and asyncio.
```

中文（投中文列表）：
```markdown
- [灵智（Lumio）](https://github.com/slowleelab/lumio) - 银行级私有化智能客服参考实现，含 RAG 机器人问答、实时坐席辅助与 22 个 MCP 信用卡工具，支持合规过滤与熔断降级链。基于 FastAPI + asyncio，`make demo` 一键体验。
```

---

## 1. mjhea0/awesome-fastapi

**小节定位**：找 `### Boilerplate` / `### Projects` 类小节（这类列表通常把完整项目放末尾"Projects/Open Source"区）。

```diff
--- a/README.md
+++ b/README.md
@@ 在该列表 Projects / Open Source 小节末尾追加（或按字母序插入）
+- [Lumio](https://github.com/slowleelab/lumio) - Self-hostable banking-grade intelligent customer service with a RAG chatbot, real-time agent assist, and 22 MCP credit-card tools, featuring compliance filtering and circuit-breaker degradation chains. Built with FastAPI and asyncio.
```

**PR 标题**：`Add Lumio`
**PR 正文**：
```
Adds Lumio, an open-source (Apache-2.0) production-grade customer-service platform built on FastAPI.

- RAG-augmented chatbot (ES BM25 + Milvus vector + RRF fusion)
- Real-time agent-assist over WebSocket
- 22 Java MCP credit-card tools (Spring AI, mock data)
- Compliance filtering, circuit-breaker degradation chains, full observability
- One-command demo: `make demo`

Repo: https://github.com/slowleelab/lumio
```

---

## 2. awesome-llm / awesome-chatgpt 类列表

**小节定位**：`### Applications` / `### Chatbots` / `### RAG`。

```diff
+- [Lumio](https://github.com/slowleelab/lumio) - Self-hostable banking-grade intelligent customer service with a RAG chatbot, real-time agent assist, and 22 MCP credit-card tools, featuring compliance filtering and circuit-breaker degradation chains. Built with FastAPI and asyncio.
```

**PR 标题**：`Add Lumio — self-hosted RAG customer service`

---

## 3. awesome-mcp-servers / awesome-spring-ai 类列表

**小节定位**：`### Servers` / `### Java`。

```diff
+- [Lumio](https://github.com/slowleelab/lumio) - Banking MCP server with 22 mock credit-card tools (bill / card / limit / installment / payment / points / transaction) on Spring AI 1.0 + Higress + Nacos.
```

**PR 标题**：`Add Lumio — 22 credit-card MCP tools on Spring AI`

---

## 4. 中文列表（如 chinese-independent-developer / 中文 awesome）

```diff
+- [灵智（Lumio）](https://github.com/slowleelab/lumio) - 银行级私有化智能客服参考实现，含 RAG 机器人问答、实时坐席辅助与 22 个 MCP 信用卡工具，支持合规过滤与熔断降级链。基于 FastAPI + asyncio，`make demo` 一键体验。
```

---

## 提交步骤（每个列表通用）

```bash
# 1. Fork 目标仓库后克隆你的 fork
git clone https://github.com/<你的账号>/awesome-xxx && cd awesome-xxx
git checkout -b add-lumio

# 2. 按上面 diff 编辑 README.md（注意小节与字母序）

# 3. 提交并推送
git add README.md
git commit -m "Add Lumio"
git push origin add-lumio

# 4. 用 gh 开 PR（在 fork 目录下）
gh pr create --repo <上游owner>/awesome-xxx \
  --title "Add Lumio" \
  --body "见上方 PR 正文模板"
```

> 投前需先 fork 目标列表仓库（PR 会公开发表），告诉我要投哪一个，我把命令跑起来。
