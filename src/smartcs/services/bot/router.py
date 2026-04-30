"""机器人服务 HTTP API 路由"""

from __future__ import annotations

from fastapi import APIRouter

from smartcs.shared.models import ChatRequest, ChatResponse

router = APIRouter(tags=["bot"])


@router.get("/health")
async def health_check():
    """机器人服务健康检查"""
    return {"status": "healthy", "service": "bot"}


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """机器人聊天接口

    Sprint 1 骨架阶段返回占位响应，后续 Sprint 3 实现完整编排逻辑。
    """
    return ChatResponse(
        session_id=request.session_id or "placeholder-session",
        reply="机器人服务已就绪，Sprint 3 将实现完整对话能力。",
        source="fallback",
    )
