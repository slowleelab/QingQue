"""审批记录写入 (P0-2.2)

从 kb.api.approval 提取, 让 documents.py (takedown/rollback) 也能复用
统一的 KbDocumentApproval 写入 + approval_status 同步逻辑.

单点真相: 所有审批动作 (workflow / takedown / rollback) 走这里写库.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import uuid_utils

from kb.logging import get_logger
from kb.orm.kb import (
    KbApprovalAction,
    KbApprovalStatus,
    KbDocument,
    KbDocumentApproval,
)

logger = get_logger(__name__)


# 高风险动作 (强制 risk_level='high')
HIGH_RISK_ACTIONS = {KbApprovalAction.ARCHIVE, KbApprovalAction.SUPERSEDE}

# 业务审计默认留存: 5 年 (GB/T 22239 三级等保)
AUDIT_RETENTION_YEARS = 5


def record_approval(
    db: Any,
    *,
    doc: KbDocument,
    action: KbApprovalAction,
    from_status: KbApprovalStatus,
    to_status: KbApprovalStatus,
    actor_id: str,
    actor_role: str,
    comment: str | None,
    tenant_id: str | None = None,
    ip: str | None = None,
    ua: str | None = None,
    request_id: str | None = None,
) -> KbDocumentApproval:
    """写入 KbDocumentApproval + 同步 KbDocument.approval_status + updated_at

    参数:
      tenant_id: 默认从 doc.tenant_id 取, 但 takedown/rollback 等场景下可显式覆盖
      ip/ua/request_id: 审计三元组, 由调用方从 Request 提取

    返回:
      写入的 KbDocumentApproval 实例 (id 已分配)
    """
    now = datetime.now(timezone.utc)
    retention = now + timedelta(days=365 * AUDIT_RETENTION_YEARS)

    record = KbDocumentApproval(
        id=uuid_utils.uuid7(),
        document_id=doc.id,
        action=action,
        from_status=from_status.value if hasattr(from_status, "value") else str(from_status),
        to_status=to_status.value if hasattr(to_status, "value") else str(to_status),
        actor_id=actor_id,
        actor_role=actor_role,
        comment=comment,
        tenant_id=tenant_id or doc.tenant_id,
        ip=ip,
        ua=(ua[:256] if ua else None),
        request_id=request_id,
        operation_result="success",
        risk_level="high" if action in HIGH_RISK_ACTIONS else "normal",
        retention_until=retention,
    )
    db.add(record)

    # 同步文档表状态
    doc.approval_status = to_status
    doc.updated_by = actor_id
    doc.updated_at = now
    return record


def validate_or_raise(
    *,
    current_status: KbApprovalStatus | str,
    action: KbApprovalAction | str,
    actor_id: str,
    actor_role: str,
    comment: str | None,
    last_actor: str | None,
) -> KbApprovalStatus:
    """状态机校验, 失败抛 HTTPException (403 / 422 区分)

    与 kb.api.approval._execute_action 内的 try/except 逻辑一致,
    但移到这里让 documents.py (takedown/rollback) 也能复用.

    Returns:
      合法时返回 new_status

    Raises:
      HTTPException 403 dual_sign_required
      HTTPException 422 illegal_transition / comment_required
      HTTPException 400 invalid_status / invalid_action
    """
    from fastapi import HTTPException

    from kb.security.workflow import WorkflowError, validate_transition

    try:
        new_status = validate_transition(
            current_status=current_status,
            action=action,
            actor_id=actor_id,
            actor_role=actor_role,
            comment=comment,
            last_actor=last_actor,
        )
    except WorkflowError as e:
        if e.code == "dual_sign_required":
            raise HTTPException(status_code=403, detail=e.message) from e
        raise HTTPException(status_code=e.http_status, detail=e.message) from e
    return new_status


__all__ = [
    "record_approval",
    "validate_or_raise",
    "HIGH_RISK_ACTIONS",
    "AUDIT_RETENTION_YEARS",
]
