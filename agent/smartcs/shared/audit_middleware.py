"""审计日志中间件

自动记录所有状态变更操作（POST/PUT/DELETE）到 audit_log 表。
GET 请求不记录（只读操作无需审计）。

集成方式：在 app 创建时调用 register_audit_middleware(app)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response

from smartcs.shared.pii import mask_pii

logger = logging.getLogger(__name__)

# 需要审计的 HTTP 方法
_AUDITED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# 不审计的路径前缀
_EXCLUDED_PATHS = {"/api/health", "/metrics", "/favicon.ico"}


def register_audit_middleware(app: FastAPI) -> None:
    """注册审计日志中间件"""

    @app.middleware("http")
    async def audit_middleware(request: Request, call_next):
        # 跳过不需要审计的请求
        if request.method not in _AUDITED_METHODS:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(ex) for ex in _EXCLUDED_PATHS):
            return await call_next(request)

        # 执行请求
        start = time.monotonic()
        response: Response = await call_next(request)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        # 异步写入审计日志（不阻塞响应）
        try:
            import asyncio

            asyncio.create_task(_write_audit_log(request, response, elapsed_ms))
        except Exception:
            logger.debug("审计日志创建任务失败: %s %s", request.method, path)

        return response


async def _write_audit_log(request: Request, response: Response, elapsed_ms: int) -> None:
    """写入审计日志到数据库"""
    from smartcs.shared.orm_models import AuditLog

    # 获取操作者信息（从 JWT 或默认）
    actor_id = "anonymous"
    actor_role = "anonymous"
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            from smartcs.shared.auth import decode_token

            payload = decode_token(auth_header[7:])
            actor_id = payload.get("sub", "anonymous")
            actor_role = payload.get("role", "anonymous")
        except Exception:
            pass
    elif request.app.state and hasattr(request.app.state, "environment"):
        from smartcs.shared.config import get_settings

        if get_settings().environment == "development":
            actor_id = "dev-user"
            actor_role = "admin"

    # 推断操作类型和目标
    action, target_type, target_id = _infer_action(request)

    # 获取数据库会话
    session_factory = getattr(request.app.state, "db_session_factory", None)
    if session_factory is None:
        return

    # 写入审计日志

    detail: dict[str, Any] = {"elapsed_ms": elapsed_ms}
    # 记录查询参数（脱敏）
    if request.query_params:
        detail["query_params"] = mask_pii(str(request.query_params))

    async with session_factory() as session:
        record = AuditLog(
            actor_id=actor_id,
            actor_role=actor_role,
            action=action,
            target_type=target_type,
            target_id=target_id,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            ip_address=request.client.host if request.client else None,
            detail=detail,
        )
        session.add(record)
        await session.commit()


def _infer_action(request: Request) -> tuple[str, str, str | None]:
    """从请求路径推断操作类型和目标

    Returns:
        (action, target_type, target_id)
    """
    path = request.url.path.strip("/")
    parts = path.split("/")
    method = request.method

    # /api/session/update → session.transition
    if "session" in parts:
        idx = parts.index("session")
        target_id = parts[idx + 1] if idx + 1 < len(parts) else None
        if "update" in parts:
            return "session.transition", "session", target_id
        return f"session.{method.lower()}", "session", target_id

    # /api/feedback → feedback.submit
    if "feedback" in parts:
        if "undo" in parts:
            return "feedback.undo", "feedback", None
        return "feedback.submit", "feedback", None

    # /api/kb/documents → document.upload
    if "kb" in parts and "documents" in parts:
        return "document.upload", "document", None

    # /api/hold → session.hold
    if "hold" in parts:
        return "session.hold", "session", None

    # /api/resume → session.resume
    if "resume" in parts:
        return "session.resume", "session", None

    # /api/review → review.submit
    if "review" in parts:
        return f"review.{method.lower()}", "review", None

    # /api/notify → notify.receive
    if "notify" in parts:
        return "notify.receive", "notify", None

    # /api/analyze → analyze.request
    if "analyze" in parts:
        return "analyze.request", "analyze", None

    # 默认
    return f"{method.lower()}.{parts[-1] if parts else 'unknown'}", "other", None
