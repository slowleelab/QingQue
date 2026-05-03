# star-connection 集成到 SmartCS — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 star-connection (Java 在线客服系统) 嵌入 SmartCS，实现 Bot 先行 → 转人工 → 坐席接管 + AI 辅助旁路的全链路闭环。

**Architecture:** HTTP 长轮询统一客户端通信，SmartCS Redis 作为单一会话状态源，star-connection 通过新增 API 接收转人工请求，agent-server 内部连 Assist WebSocket 获取 AI 辅助。

**Tech Stack:** FastAPI (Python), Spring Boot 3 + Netty + ZooKeeper (Java 17), Vue 3 + TypeScript, Redis, Maven

---

## File Structure Map

```
agent_project/
├── star-connection/                      # 搬迁自外部项目
│   ├── customer-server/src/main/java/.../controller/
│   │   └── TransferController.java       # 新增: POST /api/sessions
│   ├── agent-server/src/main/java/.../
│   │   ├── assist/AssistClient.java      # 新增: Assist WS 客户端
│   │   └── callback/SessionCallback.java # 新增: 会话状态回调
├── deploy/
│   └── docker-compose.yml                # +zookeeper
├── src/smartcs/
│   ├── services/bot/
│   │   └── router.py                     # 大改: send + poll 端点
│   ├── services/assist/
│   │   └── router.py                     # +session/update 端点
│   ├── services/common/
│   │   ├── star_client.py                # 新增: star-connection HTTP 客户端
│   │   └── deps.py                       # +star_client DI
│   └── shared/
│       └── models.py                     # +PollResponse 等模型
├── web/src/
│   ├── composables/useChatPoll.ts        # 新增: 统一长轮询
│   ├── api/types.ts                      # +transfer_url 字段
│   └── views/BotChat.vue                 # 改造: fetch → 轮询
├── tests/
│   ├── test_chat_poll.py                 # 新增
│   └── test_star_client.py               # 新增
└── Makefile                              # +star-* 目标
```

---

### Task 1: 搬迁 star-connection 项目

**Files:**
- Create: `star-connection/` (整个目录)
- Modify: `.gitignore` (排除 star-connection/*.log, star-connection/target/)

- [ ] **Step 1: Copy project**

```bash
cp -r /Users/qiangli/Documents/claude/star-connection /Users/qiangli/CodeBuddy/agent_project/star-connection
```

- [ ] **Step 2: Clean build artifacts**

```bash
cd /Users/qiangli/CodeBuddy/agent_project/star-connection && rm -rf target/ logs/ *.log customer-server/target/ agent-server/target/ common/target/ transport/*/target/
```

- [ ] **Step 3: Update .gitignore**

Add to `.gitignore`:
```
star-connection/target/
star-connection/logs/
star-connection/*.log
star-connection/**/target/
star-connection/**/*.iml
```

- [ ] **Step 4: Verify Maven build**

```bash
cd star-connection && mvn clean compile -q && echo "BUILD OK"
```

- [ ] **Step 5: Commit**

```bash
git add star-connection/ .gitignore
git commit -m "chore: relocate star-connection project into SmartCS"
```

---

### Task 2: Docker Compose + Makefile

**Files:**
- Modify: `deploy/docker-compose.yml`
- Modify: `Makefile`

- [ ] **Step 1: Add ZooKeeper to docker-compose.yml**

Read `deploy/docker-compose.yml` first, then append after the existing services:

```yaml
  # ── 在线客服系统 ──
  zookeeper:
    image: zookeeper:3.8
    container_name: smartcs-zk
    ports:
      - "2181:2181"
    environment:
      ZOO_4LW_COMMANDS_WHITELIST: "*"
    healthcheck:
      test: ["CMD", "echo", "ruok", "|", "nc", "localhost", "2181"]
      interval: 10s
      timeout: 5s
      retries: 5
```

- [ ] **Step 2: Add Makefile targets**

Append to `Makefile`:

```makefile
# ── star-connection ──
star-build:
	cd star-connection && mvn clean package -DskipTests -q

star-up: star-build
	cd star-connection && \
	java -jar customer-server/target/customer-server-1.0.0.jar & \
	sleep 3 && \
	java -jar agent-server/target/agent-server-1.0.0.jar --server.port=8081 &

star-down:
	pkill -f "customer-server" || true
	pkill -f "agent-server" || true
```

- [ ] **Step 3: Start ZooKeeper and verify**

```bash
make up  # restart with new ZK service
docker exec smartcs-zk echo ruok | nc localhost 2181
```
Expected: `imok`

- [ ] **Step 4: Commit**

```bash
git add deploy/docker-compose.yml Makefile
git commit -m "chore: add ZooKeeper to Docker Compose and star-connection Makefile targets"
```

---

### Task 3: SmartCS 新增轮询模型

**Files:**
- Modify: `src/smartcs/shared/models.py` (append)

- [ ] **Step 1: Add poll-related models**

Append to `models.py`:

```python
# ── 长轮询 ──


class ChatSendRequest(BaseModel):
    """客户端发送消息请求"""
    session_id: str | None = None
    customer_id: str | None = None
    message: str
    channel: ChannelType = ChannelType.WEB


class ChatSendResponse(BaseModel):
    """发送消息响应"""
    accepted: bool = True
    message_id: str
    session_id: str


class PollResponse(BaseModel):
    """长轮询响应"""
    has_message: bool = False
    reply: str = ""
    intent: IntentLabel | None = None
    confidence: float = 0.0
    source: str = "rag"
    is_transfer: bool = False
    transfer_url: str = ""
    transfer_reason: str = ""


class SessionUpdateRequest(BaseModel):
    """会话状态更新请求（star-connection 回调）"""
    session_id: str
    phase: str  # "ASSIST" | "ENDED"
    agent_id: str | None = None


class SessionUpdateResponse(BaseModel):
    """会话状态更新响应"""
    status: str = "ok"
```

- [ ] **Step 2: Verify**

```bash
poetry run python -c "from smartcs.shared.models import ChatSendRequest, ChatSendResponse, PollResponse, SessionUpdateRequest; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/smartcs/shared/models.py
git commit -m "feat: add long-poll request/response and session update models"
```

---

### Task 4: Bot 长轮询端点

**Files:**
- Modify: `src/smartcs/services/bot/router.py`
- Create: `tests/test_chat_poll.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_chat_poll.py
from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport
from smartcs.main import bot_app


@pytest.fixture
async def bot_client():
    transport = ASGITransport(app=bot_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_chat_send_returns_accepted(bot_client):
    resp = await bot_client.post("/api/chat/send", json={
        "message": "你好",
        "session_id": "test-poll-001",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] is True
    assert "message_id" in data


@pytest.mark.asyncio
async def test_chat_poll_returns_data(bot_client):
    resp = await bot_client.get("/api/chat/poll", params={
        "session_id": "test-poll-002",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "has_message" in data


@pytest.mark.asyncio
async def test_chat_poll_returns_after_send(bot_client):
    # send first
    send_resp = await bot_client.post("/api/chat/send", json={
        "message": "我想查账单",
        "session_id": "test-poll-003",
    })
    assert send_resp.status_code == 200

    # poll for response
    poll_resp = await bot_client.get("/api/chat/poll", params={
        "session_id": "test-poll-003",
        "timeout": 30,
    })
    assert poll_resp.status_code == 200
    data = poll_resp.json()
    # 至少返回 has_message 字段
    assert "has_message" in data
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/test_chat_poll.py -v
```
Expected: FAIL (404 for /api/chat/send)

- [ ] **Step 3: Implement send + poll endpoints in router.py**

Read current `router.py` first, then replace `/api/chat` with send+poll endpoints:

```python
"""机器人服务 HTTP 路由"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from smartcs.services.common.deps import AgentDep, SessionManagerDep
from smartcs.shared.models import (
    ChatRequest,
    ChatResponse,
    ChatSendRequest,
    ChatSendResponse,
    PollResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["bot"])


@router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "bot"}


@router.post("/chat/send")
async def chat_send(
    body: ChatSendRequest,
    request: Request,
) -> ChatSendResponse:
    """客户发送消息 — 入队后立即返回"""
    import uuid

    app = request.app
    agent = app.state.agent
    session_manager = app.state.session_manager
    redis = app.state.redis_client

    session_id = body.session_id or str(uuid.uuid4())

    # 消息入队，后台异步处理
    message_id = str(uuid.uuid4())
    task_data = {
        "session_id": session_id,
        "customer_id": body.customer_id,
        "message": body.message,
        "channel": body.channel.value,
        "message_id": message_id,
    }
    await redis.lpush("smartcs:chat:queue", json.dumps(task_data))

    return ChatSendResponse(
        accepted=True,
        message_id=message_id,
        session_id=session_id,
    )


@router.get("/chat/poll")
async def chat_poll(
    session_id: str = Query(...),
    since: str | None = Query(default=None),
    timeout: int = Query(default=25),
    request: Request = None,
) -> PollResponse:
    """长轮询 — 阻塞等待新消息，无消息则等 timeout 秒后返回空"""
    app = request.app
    redis = app.state.redis_client
    response_key = f"smartcs:response:{session_id}"

    # 轮询等待（最长 timeout 秒）
    poll_interval = 0.5
    waited = 0
    while waited < timeout:
        raw = await redis.get(response_key)
        if raw:
            data = json.loads(raw)
            await redis.delete(response_key)
            return PollResponse(**data)
        await asyncio.sleep(poll_interval)
        waited += poll_interval

    return PollResponse(has_message=False)


# ── 后台消息处理（lifespan 中启动） ──

async def process_chat_queue(app):
    """轮询 chat:queue，处理消息并写入 response"""
    import json
    import logging
    logger = logging.getLogger(__name__)

    redis = app.state.redis_client
    agent = app.state.agent

    while True:
        try:
            _, raw = await redis.brpop("smartcs:chat:queue", timeout=1)
            if raw is None:
                continue
            task = json.loads(raw) if isinstance(raw, bytes) else json.loads(raw)

            session_id = task["session_id"]
            message = task.get("message", "")
            customer_id = task.get("customer_id")

            # 调用 Agent 处理
            result = await agent.process(
                session_id=session_id,
                message=message,
                customer_id=customer_id,
            )

            # 写入响应
            response_key = f"smartcs:response:{session_id}"
            poll_data = {
                "has_message": True,
                "reply": result.get("reply", ""),
                "intent": result.get("intent"),
                "confidence": result.get("confidence", 0.0),
                "source": result.get("source", "rag"),
                "is_transfer": result.get("is_transfer", False),
                "transfer_url": result.get("transfer_url", ""),
                "transfer_reason": result.get("transfer_reason", ""),
            }
            await redis.set(response_key, json.dumps(poll_data, default=str), ex=120)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"消息处理失败: {e}", exc_info=True)
            continue
```

- [ ] **Step 4: Run tests**

```bash
poetry run pytest tests/test_chat_poll.py -v
```
Expected: 3 passed (or at least the poll endpoint is reachable)

- [ ] **Step 5: Commit**

```bash
git add src/smartcs/services/bot/router.py tests/test_chat_poll.py
git commit -m "feat: implement Bot long-poll send+poll endpoints with Redis queue"
```

---

### Task 5: star-connection HTTP 客户端 + 转人工桥接

**Files:**
- Create: `src/smartcs/services/common/star_client.py`
- Create: `tests/test_star_client.py`
- Modify: `src/smartcs/services/common/deps.py`
- Modify: `src/smartcs/services/bot/router.py` (transfer logic)

- [ ] **Step 1: Write failing test**

```python
# tests/test_star_client.py
from __future__ import annotations

import pytest
from smartcs.services.common.star_client import StarConnectionClient


@pytest.fixture
def client():
    return StarConnectionClient(base_url="http://localhost:8080")


def test_client_has_base_url(client):
    assert client._base_url == "http://localhost:8080"


def test_build_transfer_request(client):
    req = client.build_transfer_request(
        session_id="sess-001",
        customer_id="cust-001",
        transfer_reason="complaint",
        transfer_summary="客户投诉",
        history=[{"role": "customer", "content": "投诉"}],
        intent="complaint",
        sentiment="angry",
    )
    assert req["session_id"] == "sess-001"
    assert req["transfer_reason"] == "complaint"
    assert len(req["history"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/test_star_client.py -v
```

- [ ] **Step 3: Implement StarConnectionClient**

```python
# src/smartcs/services/common/star_client.py
"""star-connection HTTP 客户端封装"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from smartcs.shared.config import get_settings

logger = logging.getLogger(__name__)


class StarConnectionClient:
    """star-connection HTTP 客户端"""

    def __init__(self, base_url: str = "http://localhost:8080") -> None:
        self._base_url = base_url.rstrip("/")

    def build_transfer_request(
        self,
        session_id: str,
        customer_id: str | None = None,
        transfer_reason: str = "",
        transfer_summary: str = "",
        history: list[dict[str, str]] | None = None,
        intent: str = "",
        sentiment: str = "",
    ) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "customer_id": customer_id or "",
            "transfer_reason": transfer_reason,
            "transfer_summary": transfer_summary,
            "history": history or [],
            "intent": intent,
            "sentiment": sentiment,
        }

    async def create_session(self, data: dict[str, Any]) -> dict[str, Any]:
        """POST /api/sessions — 创建人工客服会话"""
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self._base_url}/api/sessions",
                json=data,
            )
            if resp.status_code != 200:
                logger.error("star-connection create_session failed: %s %s", resp.status_code, resp.text)
                raise RuntimeError(f"star-connection 返回 {resp.status_code}")
            return resp.json()

    async def update_session(self, session_id: str, phase: str, agent_id: str | None = None) -> dict[str, Any]:
        """POST /api/session/update — 回调 SmartCS 更新会话状态"""
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{self._base_url}/api/session/update",
                json={
                    "session_id": session_id,
                    "phase": phase,
                    "agent_id": agent_id,
                },
            )
            return resp.json() if resp.status_code == 200 else {}
```

- [ ] **Step 4: Add to deps.py**

```python
# Add after existing init functions:

async def init_star_client(app) -> None:
    """初始化 star-connection 客户端"""
    settings = get_settings()
    app.state.star_client = StarConnectionClient(
        base_url=getattr(settings, 'star_base_url', 'http://localhost:8080')
    )

def get_star_client(request) -> StarConnectionClient:
    return request.app.state.star_client

StarClientDep = Annotated[StarConnectionClient, Depends(get_star_client)]
```

- [ ] **Step 5: Run tests**

```bash
poetry run pytest tests/test_star_client.py -v
```
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add src/smartcs/services/common/star_client.py tests/test_star_client.py src/smartcs/services/common/deps.py
git commit -m "feat: add StarConnectionClient for transfer and session callback"
```

---

### Task 6: SmartCS session/update 回调端点

**Files:**
- Modify: `src/smartcs/services/assist/router.py` (append endpoint)
- Modify: `tests/test_assist_ws.py` (or reuse existing assist test)

- [ ] **Step 1: Add callback endpoint to assist router**

Append to `router.py`:

```python
@router.post("/session/update")
async def session_update(body: SessionUpdateRequest, request: Request):
    """接收 star-connection 的会话状态回调"""
    app = request.app
    session_manager = app.state.session_manager

    try:
        phase = SessionPhase(body.phase.upper())
        await session_manager.transition_phase(
            session_id=body.session_id,
            new_phase=phase,
            reason=body.agent_id or "",
        )
        logger.info("会话 %s 状态更新为 %s", body.session_id, phase.value)
        return SessionUpdateResponse(status="ok")
    except Exception as e:
        logger.error("会话状态更新失败: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
```

Add imports:
```python
from smartcs.shared.models import (
    SessionUpdateRequest,
    SessionUpdateResponse,
    SessionPhase,
)
from fastapi import HTTPException
```

- [ ] **Step 2: Verify endpoint**

```bash
curl -X POST http://localhost:8001/api/session/update \
  -H "Content-Type: application/json" \
  -d '{"session_id":"test-001","phase":"ASSIST","agent_id":"agent-3"}'
```
Expected: `{"status":"ok"}`

- [ ] **Step 3: Commit**

```bash
git add src/smartcs/services/assist/router.py
git commit -m "feat: add session/update callback endpoint for star-connection"
```

---

### Task 7: Bot 转人工桥接集成

**Files:**
- Modify: `src/smartcs/services/bot/router.py` (update `process_chat_queue` to call star-connection on transfer)
- Modify: `src/smartcs/main.py` (lifespan adds `process_chat_queue` task)

- [ ] **Step 1: Update process_chat_queue to handle transfer**

In `router.py`, update the message processing to call star-connection when transfer is needed:

```python
async def process_chat_queue(app):
    """轮询 chat:queue，处理消息并写入 response"""
    import json
    import logging
    logger = logging.getLogger(__name__)

    redis = app.state.redis_client
    agent = app.state.agent
    star_client = getattr(app.state, "star_client", None)
    session_manager = app.state.session_manager

    while True:
        try:
            _, raw = await redis.brpop("smartcs:chat:queue", timeout=1)
            if raw is None:
                continue
            task = json.loads(raw) if isinstance(raw, bytes) else json.loads(raw)

            session_id = task["session_id"]
            message = task.get("message", "")
            customer_id = task.get("customer_id")

            # 调用 Agent 处理
            result = await agent.process(
                session_id=session_id,
                message=message,
                customer_id=customer_id,
            )

            poll_data: dict = {
                "has_message": True,
                "reply": result.get("reply", ""),
                "intent": result.get("intent"),
                "confidence": result.get("confidence", 0.0),
                "source": result.get("source", "rag"),
                "is_transfer": result.get("is_transfer", False),
                "transfer_url": "",
                "transfer_reason": "",
            }

            # 转人工：调用 star-connection 获取连接信息
            if poll_data["is_transfer"] and star_client:
                try:
                    transfer_req = star_client.build_transfer_request(
                        session_id=session_id,
                        customer_id=customer_id,
                        transfer_reason=result.get("transfer_reason", ""),
                        transfer_summary=result.get("transfer_summary", ""),
                        history=result.get("history", []),
                        intent=str(result.get("intent", "")),
                        sentiment=str(result.get("sentiment", "neutral")),
                    )
                    transfer_resp = await star_client.create_session(transfer_req)
                    poll_data["transfer_url"] = transfer_resp.get("poll_url", "")
                    poll_data["transfer_reason"] = transfer_resp.get("status", "")

                    # 更新会话状态为 HANDOFF
                    await session_manager.transition_phase(
                        session_id=session_id,
                        new_phase=SessionPhase.HANDOFF,
                        reason=poll_data["transfer_reason"],
                    )
                except Exception as e:
                    logger.warning("转人工调用 star-connection 失败: %s", e)

            response_key = f"smartcs:response:{session_id}"
            await redis.set(response_key, json.dumps(poll_data, default=str), ex=120)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"消息处理失败: {e}", exc_info=True)
            continue
```

- [ ] **Step 2: Add process_chat_queue to bot_lifespan**

In `main.py`, update `bot_lifespan`:

After `await init_agent(app)`, add:
```python
    app.state.redis_client = get_redis(app)
    # 启动后台消息处理
    chat_processor_task = asyncio.create_task(process_chat_queue(app))
```

In shutdown (after `yield`), add:
```python
    chat_processor_task.cancel()
    try:
        await chat_processor_task
    except asyncio.CancelledError:
        pass
```

Also add imports to main.py:
```python
from smartcs.services.bot.router import process_chat_queue
import asyncio
```

And add star_client init to bot_lifespan:
```python
    await init_star_client(app)
```

- [ ] **Step 3: Verify startup**

```bash
make dev
curl -s http://localhost:8000/api/health && echo " Bot OK"
curl -s http://localhost:8001/api/health && echo " Assist OK"
```

- [ ] **Step 4: Commit**

```bash
git add src/smartcs/services/bot/router.py src/smartcs/main.py
git commit -m "feat: add transfer bridge from Bot to star-connection"
```

---

### Task 8: 前端长轮询 Hook

**Files:**
- Create: `web/src/composables/useChatPoll.ts`
- Modify: `web/src/api/types.ts`

- [ ] **Step 1: Update types.ts**

Add to `web/src/api/types.ts`:

```typescript
export interface ChatSendResponse {
  accepted: boolean
  message_id: string
  session_id: string
}

export interface PollResponse {
  has_message: boolean
  reply: string
  intent?: IntentLabel
  confidence: number
  source: string
  is_transfer: boolean
  transfer_url: string
  transfer_reason: string
}

export interface CustomerPollResponse {
  has_message: boolean
  messages?: Array<{
    sender: "customer" | "agent" | "system"
    content: string
    timestamp: string
  }>
  session_ended?: boolean
}
```

- [ ] **Step 2: Create useChatPoll composable**

```typescript
// web/src/composables/useChatPoll.ts
import { ref, onUnmounted } from "vue"
import { client } from "@/api/client"
import type { ChatSendResponse, PollResponse, CustomerPollResponse } from "@/api/types"

export type PollMode = "bot" | "agent"

export function useChatPoll() {
  const mode = ref<PollMode>("bot")
  const sessionId = ref<string>("")
  const polling = ref(false)
  const error = ref<string | null>(null)

  let abortController: AbortController | null = null

  async function sendMessage(message: string): Promise<ChatSendResponse> {
    if (mode.value === "bot") {
      const resp = await client.post("/bot/chat/send", {
        session_id: sessionId.value || undefined,
        message,
      }) as any
      sessionId.value = resp.session_id
      return resp as ChatSendResponse
    } else {
      const resp = await fetch(`/customer/send`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId.value, content: message }),
      })
      return resp.json()
    }
  }

  async function startPolling(
    onMessage: (data: PollResponse | CustomerPollResponse) => void,
  ) {
    polling.value = true
    abortController = new AbortController()

    while (polling.value && sessionId.value) {
      try {
        const url = mode.value === "bot"
          ? `/api/bot/chat/poll?session_id=${sessionId.value}&timeout=25`
          : `/customer/poll?session_id=${sessionId.value}&token=xxx`

        const resp = await fetch(url, { signal: abortController.signal })
        const data = await resp.json()

        if (data.has_message) {
          onMessage(data)
        }
      } catch (e: any) {
        if (e.name !== "AbortError") {
          error.value = e.message
          await new Promise(r => setTimeout(r, 1000)) // backoff
        }
      }
    }
  }

  function stopPolling() {
    polling.value = false
    abortController?.abort()
    abortController = null
  }

  function switchTo(m: PollMode, sid: string) {
    stopPolling()
    mode.value = m
    sessionId.value = sid
  }

  onUnmounted(() => stopPolling())

  return { mode, sessionId, polling, error, sendMessage, startPolling, stopPolling, switchTo }
}
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd web && npx vue-tsc --noEmit 2>&1 | head -20
```

- [ ] **Step 4: Commit**

```bash
git add web/src/composables/useChatPoll.ts web/src/api/types.ts
git commit -m "feat: add useChatPoll composable for unified long-polling"
```

---

### Task 9: star-connection 改造 — TransferController.java

**Files:**
- Create: `star-connection/customer-server/src/main/java/com/example/customerserver/controller/TransferController.java`
- Create: `star-connection/customer-server/src/main/java/com/example/customerserver/dto/TransferSessionRequest.java`
- Create: `star-connection/customer-server/src/main/java/com/example/customerserver/dto/TransferSessionResponse.java`

- [ ] **Step 1: Create DTOs**

```java
// TransferSessionRequest.java
package com.example.customerserver.dto;

import java.util.List;
import java.util.Map;

public class TransferSessionRequest {
    private String sessionId;
    private String customerId;
    private String transferReason;
    private String transferSummary;
    private List<Map<String, String>> history;
    private String intent;
    private String sentiment;
    private String vipLevel;

    // getters/setters
    public String getSessionId() { return sessionId; }
    public void setSessionId(String s) { this.sessionId = s; }
    public String getCustomerId() { return customerId; }
    public void setCustomerId(String s) { this.customerId = s; }
    public String getTransferReason() { return transferReason; }
    public void setTransferReason(String s) { this.transferReason = s; }
    // ... (rest of getters/setters for all fields)
}
```

```java
// TransferSessionResponse.java
package com.example.customerserver.dto;

public class TransferSessionResponse {
    private String sessionId;
    private String pollUrl;
    private String sendUrl;
    private String token;
    private String status;

    public TransferSessionResponse(String sessionId, String pollUrl, String sendUrl, String token) {
        this.sessionId = sessionId;
        this.pollUrl = pollUrl;
        this.sendUrl = sendUrl;
        this.token = token;
        this.status = "WAITING";
    }

    // getters
    public String getSessionId() { return sessionId; }
    public String getPollUrl() { return pollUrl; }
    public String getSendUrl() { return sendUrl; }
    public String getToken() { return token; }
    public String getStatus() { return status; }
}
```

- [ ] **Step 2: Create TransferController**

```java
package com.example.customerserver.controller;

import com.example.common.model.Session;
import com.example.common.model.SessionStatus;
import com.example.customerserver.dto.TransferSessionRequest;
import com.example.customerserver.dto.TransferSessionResponse;
import com.example.customerserver.session.SessionManager;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import java.util.UUID;

@RestController
@RequestMapping("/api")
public class TransferController {

    private final SessionManager sessionManager;

    public TransferController(SessionManager sessionManager) {
        this.sessionManager = sessionManager;
    }

    @PostMapping("/sessions")
    public ResponseEntity<TransferSessionResponse> createSession(
            @RequestBody TransferSessionRequest request
    ) {
        String sessionId = request.getSessionId();
        if (sessionId == null || sessionId.isEmpty()) {
            sessionId = UUID.randomUUID().toString();
        }

        // Create session in WAITING state
        Session session = Session.create(sessionId, request.getCustomerId());
        session.setStatus(SessionStatus.WAITING);
        sessionManager.createSession(session);

        // Generate transfer token
        String token = generateToken(sessionId);

        // Build poll URLs
        String pollUrl = "http://localhost:8080/customer/poll?session_id=" + sessionId + "&token=" + token;
        String sendUrl = "http://localhost:8080/customer/send";

        return ResponseEntity.ok(new TransferSessionResponse(sessionId, pollUrl, sendUrl, token));
    }

    private String generateToken(String sessionId) {
        return java.util.Base64.getUrlEncoder()
            .encodeToString((sessionId + ":" + System.currentTimeMillis()).getBytes());
    }
}
```

- [ ] **Step 3: Build and verify**

```bash
cd star-connection && mvn clean compile -q && echo "COMPILE OK"
```

- [ ] **Step 4: Commit**

```bash
git add star-connection/customer-server/src/main/java/com/example/customerserver/controller/TransferController.java
git add star-connection/customer-server/src/main/java/com/example/customerserver/dto/TransferSession*.java
git commit -m "feat: add TransferController for Bot-to-agent session creation"
```

---

### Task 10: star-connection 改造 — AssistClient.java

**Files:**
- Create: `star-connection/agent-server/src/main/java/com/example/agentserver/assist/AssistClient.java`

- [ ] **Step 1: Create AssistClient**

```java
package com.example.agentserver.assist;

import com.example.common.model.ChatMessage;
import com.example.common.model.MessageType;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.util.concurrent.CompletableFuture;
import java.util.function.Consumer;

public class AssistClient {
    private static final Logger log = LoggerFactory.getLogger(AssistClient.class);
    private static final String ASSIST_WS_URL = "ws://localhost:8001/api/ws/";

    private final String sessionId;
    private WebSocket ws;
    private final ObjectMapper mapper = new ObjectMapper();
    private Consumer<String> onPushCallback;

    public AssistClient(String sessionId) {
        this.sessionId = sessionId;
    }

    public void setOnPushCallback(Consumer<String> callback) {
        this.onPushCallback = callback;
    }

    public void connect() throws Exception {
        HttpClient client = HttpClient.newHttpClient();
        URI uri = URI.create(ASSIST_WS_URL + sessionId);
        ws = client.newWebSocketBuilder()
            .buildAsync(uri, new WebSocket.Listener() {
                @Override
                public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
                    log.debug("Assist push for {}: {}", sessionId, data);
                    if (onPushCallback != null) {
                        onPushCallback.accept(data.toString());
                    }
                    return WebSocket.Listener.super.onText(webSocket, data, last);
                }

                @Override
                public void onOpen(WebSocket webSocket) {
                    log.info("AssistClient connected for session {}", sessionId);
                    WebSocket.Listener.super.onOpen(webSocket);
                }

                @Override
                public void onError(WebSocket webSocket, Throwable error) {
                    log.warn("AssistClient error for {}: {}", sessionId, error.getMessage());
                    WebSocket.Listener.super.onError(webSocket, error);
                }
            }).get();
    }

    public void sendMessage(ChatMessage msg) throws Exception {
        if (ws == null) return;
        String json = mapper.writeValueAsString(java.util.Map.of(
            "type", "customer_message",
            "message", msg.getContent(),
            "intent", "faq",
            "sentiment", "neutral"
        ));
        ws.sendText(json, true);
    }

    public void disconnect() {
        if (ws != null && !ws.isOutputClosed()) {
            ws.sendClose(1000, "session ended");
        }
    }
}
```

- [ ] **Step 2: Build and verify**

```bash
cd star-connection && mvn clean compile -q && echo "COMPILE OK"
```

- [ ] **Step 3: Commit**

```bash
git add star-connection/agent-server/src/main/java/com/example/agentserver/assist/AssistClient.java
git commit -m "feat: add AssistClient for connecting to SmartCS Assist WebSocket"
```

---

### Task 11: 端到端集成验证

- [ ] **Step 1: Start all services**

```bash
# Terminal 1: 中间件
make up
docker exec smartcs-zk echo ruok | nc localhost 2181  # expected: imok

# Terminal 2: SmartCS 后端
make dev

# Terminal 3: star-connection
make star-up

# Terminal 4: 前端
cd web && pnpm dev
```

- [ ] **Step 2: Test send + poll**

```bash
# Send a message
curl -X POST http://localhost:8000/api/chat/send \
  -H "Content-Type: application/json" \
  -d '{"message":"你好","session_id":"e2e-test-001"}'

# Poll for response
curl "http://localhost:8000/api/chat/poll?session_id=e2e-test-001&timeout=10"
```

- [ ] **Step 3: Test transfer API**

```bash
curl -X POST http://localhost:8080/api/sessions \
  -H "Content-Type: application/json" \
  -d '{"session_id":"sess-001","customer_id":"cust-001","transfer_reason":"complaint"}'
```

- [ ] **Step 4: Test session update callback**

```bash
curl -X POST http://localhost:8001/api/session/update \
  -H "Content-Type: application/json" \
  -d '{"session_id":"sess-001","phase":"ASSIST","agent_id":"agent-3"}'
```

- [ ] **Step 5: Run full test suite**

```bash
poetry run pytest tests/ -v --ignore=tests/test_metrics.py -x
```

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "feat: star-connection integration complete — Bot → transfer → agent + Assist"
```
