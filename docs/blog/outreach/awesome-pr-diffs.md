# Awesome-List 投稿 PR（可直接套用）

> 网络受限未能拉取各列表实时 README，以下 diff 基于 sindresorhus/awesome 通用规范编写。
> **提交前请打开目标列表的 CONTRIBUTING.md 核对 3 点**：① 描述结尾是否带句号；② 是否要求按字母序插入；③ 插入到哪个小节。多数列表差异只在这三处。

---

## 通用条目（中英两版，选一）

英文（投国际列表）：
```markdown
- [SmartCS (QingQue)](https://github.com/slowleelab/QingQue) - Self-hostable banking-grade intelligent customer service with a RAG chatbot and real-time agent assist, featuring compliance filtering and circuit-breaker degradation chains. Built with FastAPI and LangGraph.
```

中文（投中文列表）：
```markdown
- [SmartCS（青雀）](https://github.com/slowleelab/QingQue) - 银行级私有化智能客服参考实现，含 RAG 机器人问答与实时坐席辅助，支持合规过滤与熔断降级链。基于 FastAPI + LangGraph，`make demo` 一键体验。
```

---

## 1. mjhea0/awesome-fastapi

**小节定位**：找 `### Boilerplate` / `### Projects` 类小节（这类列表通常把完整项目放末尾"Projects/Open Source"区）。

```diff
--- a/README.md
+++ b/README.md
@@ 在该列表 Projects / Open Source 小节末尾追加（或按字母序插入）
+- [SmartCS (QingQue)](https://github.com/slowleelab/QingQue) - Self-hostable banking-grade intelligent customer service with a RAG chatbot and real-time agent assist, featuring compliance filtering and circuit-breaker degradation chains. Built with FastAPI and LangGraph.
```

**PR 标题**：`Add SmartCS (QingQue)`
**PR 正文**：
```
Adds SmartCS (QingQue), an open-source (Apache-2.0) production-grade customer-service platform built on FastAPI.

- RAG-augmented chatbot (ES BM25 + Milvus vector + RRF fusion)
- Real-time agent-assist over WebSocket
- Compliance filtering, circuit-breaker degradation chains, full observability
- One-command demo: `make demo`

Repo: https://github.com/slowleelab/QingQue
```

---

## 2. awesome-llm / awesome-chatgpt 类列表

**小节定位**：`### Applications` / `### Chatbots` / `### RAG`。

```diff
+- [SmartCS (QingQue)](https://github.com/slowleelab/QingQue) - Self-hostable banking-grade intelligent customer service with a RAG chatbot and real-time agent assist, featuring compliance filtering and circuit-breaker degradation chains. Built with FastAPI and LangGraph.
```

**PR 标题**：`Add SmartCS (QingQue) — self-hosted RAG customer service`

---

## 3. 中文列表（如 chinese-independent-developer / 中文 awesome）

```diff
+- [SmartCS（青雀）](https://github.com/slowleelab/QingQue) - 银行级私有化智能客服参考实现，含 RAG 机器人问答与实时坐席辅助，支持合规过滤与熔断降级链。基于 FastAPI + LangGraph，`make demo` 一键体验。
```

---

## 提交步骤（每个列表通用）

```bash
# 1. Fork 目标仓库后克隆你的 fork
git clone https://github.com/<你的账号>/awesome-xxx && cd awesome-xxx
git checkout -b add-smartcs

# 2. 按上面 diff 编辑 README.md（注意小节与字母序）

# 3. 提交并推送
git add README.md
git commit -m "Add SmartCS (QingQue)"
git push origin add-smartcs

# 4. 用 gh 开 PR（在 fork 目录下）
gh pr create --repo <上游owner>/awesome-xxx \
  --title "Add SmartCS (QingQue)" \
  --body "见上方 PR 正文模板"
```

> ⚠️ 我可以帮你执行 git/gh 命令，但需要你先确认目标列表并 fork（fork 是对外动作，且 PR 会公开发表）。告诉我投哪一个，我把命令跑起来。
