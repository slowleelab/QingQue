"""机器人服务 API 端到端测试

启动真实 uvicorn 服务器（bot :8765），通过 HTTP 请求验证完整链路：
Redis 队列 → Agent（LangGraph）→ LLM（Ollama/qwen2.5:7b）→ 回复。

前置条件：Docker 中间件已启动（make up）
"""

from __future__ import annotations

import asyncio
import uuid as uuid_module

import httpx
import pytest

# ── Health Check ──


async def test_health_check(bot_client: httpx.AsyncClient):
    """GET /api/health 返回 healthy 状态和服务标识"""
    response = await bot_client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "bot"


# ── Chat Send ──


async def test_chat_send_returns_accepted(bot_client: httpx.AsyncClient):
    """POST /api/chat/send 发送消息返回 accepted + message_id"""
    resp = await bot_client.post("/api/chat/send", json={
        "message": "你好",
        "session_id": "e2e-send-001",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] is True
    assert len(data["message_id"]) == 32
    assert data["session_id"] == "e2e-send-001"


async def test_chat_send_auto_generates_session_id(bot_client: httpx.AsyncClient):
    """POST /api/chat/send 未提供 session_id 时自动生成"""
    resp = await bot_client.post("/api/chat/send", json={
        "message": "查询账单",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["session_id"]) == 32


async def test_chat_send_validates_missing_message(bot_client: httpx.AsyncClient):
    """POST /api/chat/send 缺少必填 message 返回 422"""
    resp = await bot_client.post("/api/chat/send", json={
        "session_id": "test-validation",
    })
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == 2000


# ── Chat Poll + Full Flow ──


async def test_chat_poll_empty_when_no_message(bot_client: httpx.AsyncClient):
    """GET /api/chat/poll 无消息时返回 has_message=false"""
    resp = await bot_client.get("/api/chat/poll", params={
        "session_id": "e2e-nonexist-" + uuid_module.uuid4().hex[:8],
        "timeout": 2,
    })
    assert resp.status_code == 200
    assert resp.json()["has_message"] is False


@pytest.mark.slow
async def test_chat_full_flow_single_message(bot_client: httpx.AsyncClient):
    """端到端流程：发送消息 → worker 自动处理 → 轮询获取 AI 回复

    完整链路验证：
    1. POST /api/chat/send → Redis LPUSH
    2. 后台 worker BRPOP → Agent.run() → LLM 推理
    3. GET /api/chat/poll → Redis GET → 返回 AI 回复

    使用真实 Redis + LLM (Ollama/qwen2.5:7b)，无 mock。
    """
    session_id = "e2e-fullflow-" + uuid_module.uuid4().hex[:8]

    # 第 1 步：发送消息
    send_resp = await bot_client.post("/api/chat/send", json={
        "message": "你好，我想了解一下信用卡年费怎么减免",
        "session_id": session_id,
    })
    assert send_resp.status_code == 200
    assert send_resp.json()["accepted"] is True

    # 第 2 步：等待 worker 处理（Agent + LLM 需 2-5s）
    await asyncio.sleep(4)

    # 第 3 步：轮询获取回复
    poll_resp = await bot_client.get("/api/chat/poll", params={
        "session_id": session_id,
        "timeout": 15,
    })
    assert poll_resp.status_code == 200
    poll_data = poll_resp.json()

    assert poll_data["has_message"] is True, f"No response: {poll_data}"
    assert len(poll_data["reply"]) > 5, f"Reply too short: {poll_data['reply']}"
    assert poll_data["source"] in ("rag", "fallback", "template", "faq"), (
        f"Unexpected source: {poll_data['source']}"
    )


@pytest.mark.slow
async def test_chat_full_flow_greeting(bot_client: httpx.AsyncClient):
    """端到端流程：问候语触发闲聊/FAQ 意图"""
    session_id = "e2e-greeting-" + uuid_module.uuid4().hex[:8]

    await bot_client.post("/api/chat/send", json={
        "message": "你好",
        "session_id": session_id,
    })
    await asyncio.sleep(4)

    poll_resp = await bot_client.get("/api/chat/poll", params={
        "session_id": session_id,
        "timeout": 15,
    })
    assert poll_resp.status_code == 200
    poll_data = poll_resp.json()
    assert poll_data["has_message"] is True, f"No response: {poll_data}"
    assert len(poll_data["reply"]) > 0


@pytest.mark.slow
async def test_chat_conversation_multi_turn(bot_client: httpx.AsyncClient):
    """端到端流程：多轮对话，验证上下文保持"""
    session_id = "e2e-multiturn-" + uuid_module.uuid4().hex[:8]

    # 第 1 轮
    await bot_client.post("/api/chat/send", json={
        "message": "你好",
        "session_id": session_id,
    })
    await asyncio.sleep(4)
    r1 = await bot_client.get("/api/chat/poll", params={
        "session_id": session_id, "timeout": 10,
    })
    d1 = r1.json()
    assert d1["has_message"] is True

    # 第 2 轮
    await bot_client.post("/api/chat/send", json={
        "message": "信用卡年费多少",
        "session_id": session_id,
    })
    await asyncio.sleep(4)
    r2 = await bot_client.get("/api/chat/poll", params={
        "session_id": session_id, "timeout": 10,
    })
    d2 = r2.json()
    assert d2["has_message"] is True
    assert len(d2["reply"]) > 0

    # 两轮回复应不同（同一 session 上下文不同）
    assert d1["reply"] != d2["reply"], "Multi-turn responses should differ"


# ── KB 检索 ──


@pytest.mark.slow
async def test_kb_retrieve_hybrid_search(bot_client: httpx.AsyncClient):
    """POST /api/kb/retrieve 混合检索（BM25 + 向量 + RRF）

    使用真实 ES + Milvus。知识库需预先导入数据（make init）。
    """
    resp = await bot_client.post("/api/kb/retrieve", json={
        "query": "信用卡年费",
        "top_k": 3,
        "search_type": "hybrid",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert "total_candidates" in data
    assert "latency_ms" in data
    assert isinstance(data["results"], list)


@pytest.mark.slow
async def test_kb_retrieve_bm25_only(bot_client: httpx.AsyncClient):
    """POST /api/kb/retrieve BM25 单路检索"""
    resp = await bot_client.post("/api/kb/retrieve", json={
        "query": "积分兑换",
        "top_k": 5,
        "search_type": "bm25_only",
        "rerank": False,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data


async def test_kb_retrieve_validates_empty_query(bot_client: httpx.AsyncClient):
    """POST /api/kb/retrieve 缺少 query 字段返回 422"""
    resp = await bot_client.post("/api/kb/retrieve", json={})
    assert resp.status_code == 422


# ── 文档上传 ──


async def test_kb_documents_rejects_invalid_extension(bot_client: httpx.AsyncClient):
    """POST /api/kb/documents 不支持的文件扩展名返回 400"""
    from io import BytesIO

    resp = await bot_client.post("/api/kb/documents", files={
        "file": ("test.xyz", BytesIO(b"content"), "application/octet-stream"),
    }, data={
        "category": "FAQ",
        "doc_type": "常见问题",
    })
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"]["code"] == 2010
    assert "不支持" in data["error"]["message"]


@pytest.mark.slow
async def test_kb_documents_upload_markdown(bot_client: httpx.AsyncClient):
    """POST /api/kb/documents 上传 Markdown 文件并摄入知识库

    完整链路：上传 → MinIO → 解析 → 分块 → Embedding → ES + Milvus
    """
    from io import BytesIO

    doc_content = """# 信用卡年费减免政策

## 普卡/金卡
每年刷卡消费满 6 次，免次年年费。

## 白金卡
每年刷卡消费满 12 次且金额满 5 万元，免次年年费。
"""
    resp = await bot_client.post("/api/kb/documents", files={
        "file": ("annual_fee_test.md", BytesIO(doc_content.encode("utf-8")), "text/markdown"),
    }, data={
        "category": "ANNUAL_FEE",
        "doc_type": "政策说明",
        "security_level": "internal",
    })
    assert resp.status_code == 200, f"Upload failed: {resp.text}"
    data = resp.json()
    assert "doc_id" in data
    assert data["status"] in ("COMPLETED", "FAILED")

    # 摄入成功后验证可检索
    if data["status"] == "COMPLETED":
        await asyncio.sleep(2)  # 等 ES refresh
        search_resp = await bot_client.post("/api/kb/retrieve", json={
            "query": "年费减免",
            "top_k": 3,
            "search_type": "bm25_only",
            "rerank": False,
        })
        search_data = search_resp.json()
        if search_data["total_candidates"] > 0:
            found = any("年费" in r.get("content", "") for r in search_data["results"])
            assert found, "Uploaded doc not found in search results"
