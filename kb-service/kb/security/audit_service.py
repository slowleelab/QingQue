"""业务审计服务 (I1-C4)

统一入口, 业务端点调 AuditService.log / log_retrieval 完成审计落表.
与 AuditMiddleware (请求级) 互补: 中间件记 API 调用, AuditService 记业务事件.

- log: 通用业务事件 (审批/配置变更/角色调整等)
- log_retrieval: 检索事件 (高频, 单独路径, 写 KbRetrievalAudit)

设计原则:
- append-only: 不暴露 update/delete 接口
- 失败降级: 落表失败不能阻塞业务主链路, 仅 ERROR 日志 + 计数指标
- query_hash 不存原文: 防 PII 进日志
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import uuid_utils
from sqlalchemy.ext.asyncio import AsyncSession

from kb.logging import get_logger
from kb.orm.kb import KbRetrievalAudit

if TYPE_CHECKING:
    from fastapi import Request

logger = get_logger(__name__)


def _hash_query(query: str) -> str:
    """md5(query) — 存 hash 不存原文 (防 PII 进日志)"""
    return hashlib.md5(query.encode("utf-8")).hexdigest()  # noqa: S324


def extract_request_meta(request: "Request") -> tuple[str | None, str | None, str | None]:
    """P0-3: 从 FastAPI Request 提取 IP / UA / request_id (跨模块复用)

    优先级:
    - IP:  client.host (如有反向代理需后续加 X-Forwarded-For, P0-4 留)
    - UA:  request.headers['user-agent']
    - RID: X-Request-ID 头 > request.state.request_id (由 AuditMiddleware 注入)

    Returns:
      (ip, ua, request_id) — 任一可缺失 (返回 None)
    """
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    rid = request.headers.get("x-request-id") or getattr(request.state, "request_id", None)
    return ip, ua, rid


class AuditService:
    """业务审计统一入口

    用法:
        audit = AuditService(db)
        await audit.log_retrieval(
            principal=principal, query="信用卡额度",
            top_k=10, result_count=8, latency_ms=42,
            search_type="hybrid", degraded=False,
        )
        await audit.commit()  # 业务端点统一 commit
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._pending_count = 0

    async def log_retrieval(
        self,
        *,
        principal: Any,
        query: str,
        top_k: int,
        result_count: int,
        latency_ms: int,
        search_type: str | None = None,
        degraded: bool = False,
        request_id: str | None = None,
    ) -> None:
        """记录一次检索事件 (写 KbRetrievalAudit 表)

        参数:
          principal: Principal dataclass (actor_id, tenant_id, ...)
          query: 原始 query (只存 md5 哈希)
          top_k / result_count: 检索参数与命中数
          latency_ms: 端到端时延
          search_type: hybrid / bm25_only / vector_only
          degraded: 是否降级命中
          request_id: 来自 AuditMiddleware, 用于串联日志
        """
        try:
            record = KbRetrievalAudit(
                id=uuid_utils.uuid7(),
                request_id=request_id,
                actor_id=principal.actor_id,
                tenant_id=principal.tenant_id,
                query_hash=_hash_query(query),
                top_k=top_k,
                result_count=result_count,
                latency_ms=latency_ms,
                search_type=search_type,
                degraded=degraded,
            )
            self.db.add(record)
            self._pending_count += 1
            # P0-1: 额外记 actor_roles 到 structlog (不落 DB, 避免 alembic 迁移)
            # 风控需要"谁以什么角色查了"的可观测性, 但角色列表变更频繁不存主表
            logger.info(
                "retrieval_audit",
                actor_id=principal.actor_id,
                tenant_id=principal.tenant_id,
                actor_roles=list(getattr(principal, "roles", []) or []),
                result_count=result_count,
                search_type=search_type,
                degraded=degraded,
                request_id=request_id,
            )
        except Exception:
            # 审计落表失败不阻塞业务, 仅记 ERROR
            logger.exception("检索审计落表失败", actor_id=principal.actor_id)

    async def log_degradation(
        self,
        *,
        from_stage: str,
        to_stage: str,
        reason: str,
        principal: Any | None = None,
        request_id: str | None = None,
        search_type: str | None = None,
    ) -> None:
        """记录检索降级事件 (I2-C2)

        降级事件高频 (熔断/超时每次都触发), 不写 DB, 仅走 structlog JSON.
        业务侧从 KbRetrievalAudit.degraded 字段观测, Prometheus 看 from/to 分布.

        参数:
          from_stage: 降级前阶段 (hybrid / bm25 / rerank)
          to_stage: 降级后阶段 (bm25 / empty / no_rerank)
          reason: 触发原因 (timeout / exception / breaker_open)
          principal: 操作人 (可为 None, 异步后台任务)
          request_id: 串联上游请求
          search_type: 请求类型 (hybrid / bm25_only / vector_only)
        """
        log_kwargs: dict[str, Any] = {
            "from_stage": from_stage,
            "to_stage": to_stage,
            "reason": reason,
            "request_id": request_id,
            "search_type": search_type,
        }
        if principal is not None:
            log_kwargs["actor_id"] = principal.actor_id
            log_kwargs["actor_role"] = principal.actor_role
            log_kwargs["tenant_id"] = principal.tenant_id
        logger.warning("retrieval_degradation", **log_kwargs)

    async def log(
        self,
        *,
        event_type: str,
        principal: Any,
        resource: str | None = None,
        action: str | None = None,
        result: str = "success",
        detail: dict[str, Any] | None = None,
        request_id: str | None = None,
        ip: str | None = None,
        ua: str | None = None,
        operation_id: str | None = None,
    ) -> None:
        """通用业务事件审计

        写入 structlog (落 stdout/JSON 日志), 关键事件可加 DB 落表 (此处用日志,
        因为审批已经走 KbDocumentApproval 专用表, 通用事件不需要再开新表).

        参数:
          event_type: 业务事件类型, 如 "document.publish" / "user.role_change"
          principal: 操作人
          resource: 资源 ID (doc_id / user_id / ...)
          action: 动作描述
          result: success / denied / failed
          detail: 补充字段 (敏感信息需自行 redact)
          request_id / ip / ua: 与 AuditMiddleware 串联
          operation_id: P0-3 — 多步操作串联 (一次业务旅程共用一个 id)
        """
        logger.info(
            "business_audit",
            event_type=event_type,
            actor_id=principal.actor_id,
            actor_role=principal.actor_role,
            tenant_id=principal.tenant_id,
            resource=resource,
            action=action,
            result=result,
            request_id=request_id,
            ip=ip,
            ua=ua,
            operation_id=operation_id,
            detail=detail or {},
        )

    @property
    def pending_count(self) -> int:
        """本次会话中待落表的审计记录数"""
        return self._pending_count


__all__ = ["AuditService", "_hash_query", "extract_request_meta"]
