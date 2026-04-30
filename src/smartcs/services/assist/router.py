"""坐席辅助服务 HTTP/WebSocket 路由"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["assist"])


@router.get("/health")
async def health_check():
    """坐席辅助服务健康检查"""
    return {"status": "healthy", "service": "assist"}


@router.websocket("/ws/{session_id}")
async def assist_websocket(websocket: WebSocket, session_id: str):
    """坐席辅助 WebSocket 推送接口

    Sprint 1 骨架阶段仅建立连接，后续 Sprint 5 实现完整推送逻辑。
    """
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_json({
                "type": "assist_push",
                "session_id": session_id,
                "message": "坐席辅助服务已就绪，Sprint 5 将实现完整辅助推送。",
            })
    except WebSocketDisconnect:
        pass
