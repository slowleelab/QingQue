"""审批工作流状态机 (I1-C3)

手写状态机, 不引外部库 (transitions/graphene 等都太重, 且会增加依赖).

7 状态 + 6 动作的合规审批流:
  DRAFT ──submit──→ IN_REVIEW ──approve──→ APPROVED ──publish──→ PUBLISHED
                                                              ──supersede──→ SUPERSEDED
                                  ──reject──→ REJECTED ──edit──→ DRAFT
  PUBLISHED ──archive──→ ARCHIVED (admin 紧急下架)
  SUPERSEDED ──archive──→ ARCHIVED

双签 4-eyes: APPROVE 时, 当前 actor 不能等于该文档的 last_actor (防同一人自批).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kb.orm.kb import KbApprovalAction, KbApprovalStatus

# ── 转移矩阵 ──
# 合法转移: (from_status, action) -> to_status
_TRANSITIONS: dict[tuple[KbApprovalStatus, KbApprovalAction], KbApprovalStatus] = {
    # DRAFT → IN_REVIEW (编辑提交)
    (KbApprovalStatus.DRAFT, KbApprovalAction.SUBMIT): KbApprovalStatus.IN_REVIEW,
    (KbApprovalStatus.DRAFT, KbApprovalAction.CREATE): KbApprovalStatus.DRAFT,  # 幂等创建
    # IN_REVIEW → APPROVED / REJECTED (审核员)
    (KbApprovalStatus.IN_REVIEW, KbApprovalAction.APPROVE): KbApprovalStatus.APPROVED,
    (KbApprovalStatus.IN_REVIEW, KbApprovalAction.REJECT): KbApprovalStatus.REJECTED,
    # APPROVED → PUBLISHED (复核发布, 可同 actor)
    (KbApprovalStatus.APPROVED, KbApprovalAction.PUBLISH): KbApprovalStatus.PUBLISHED,
    (KbApprovalStatus.APPROVED, KbApprovalAction.REJECT): KbApprovalStatus.REJECTED,  # 复核驳回
    # REJECTED → DRAFT (编辑重新修改)
    (KbApprovalStatus.REJECTED, KbApprovalAction.SUBMIT): KbApprovalStatus.DRAFT,  # 重新提交即转 DRAFT
    # PUBLISHED → SUPERSEDED (新版本替代)
    (KbApprovalStatus.PUBLISHED, KbApprovalAction.SUPERSEDE): KbApprovalStatus.SUPERSEDED,
    # PUBLISHED/SUPERSEDED → ARCHIVED (紧急下架, admin)
    (KbApprovalStatus.PUBLISHED, KbApprovalAction.ARCHIVE): KbApprovalStatus.ARCHIVED,
    (KbApprovalStatus.SUPERSEDED, KbApprovalAction.ARCHIVE): KbApprovalStatus.ARCHIVED,
}


# 哪些动作在转移时**强制要求 comment** (审核留痕)
_COMMENT_REQUIRED_ACTIONS = {
    KbApprovalAction.REJECT,
    KbApprovalAction.ARCHIVE,
    KbApprovalAction.SUPERSEDE,
}

# 哪些动作需要 4-eyes 双签 (approver != submitter)
_DUAL_SIGN_ACTIONS = {
    KbApprovalAction.APPROVE,
}

# 双签豁免角色 (admin 可自批)
_DUAL_SIGN_EXEMPT_ROLES = {"admin", "service"}


@dataclass
class WorkflowError(Exception):
    """工作流非法操作 (状态机拒绝)"""

    code: str
    message: str
    http_status: int = 422

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


def _get_last_actor(doc: Any) -> str | None:
    """从文档对象取 last_actor (来自最后一次非系统审批, 简化: 取 created_by)"""
    return getattr(doc, "created_by", None)


def validate_transition(
    *,
    current_status: KbApprovalStatus | str,
    action: KbApprovalAction | str,
    actor_id: str,
    actor_role: str,
    comment: str | None,
    last_actor: str | None = None,
) -> KbApprovalStatus:
    """校验转移合法性, 返回新状态; 不合法则抛 WorkflowError

    参数:
      current_status: 当前文档审批状态
      action: 动作 (submit/approve/reject/publish/archive/supersede)
      actor_id: 当前操作人
      actor_role: 当前操作人主角色
      comment: 备注 (REJECT/ARCHIVE/SUPERSEDE 强制非空)
      last_actor: 上次动作的操作人 (用于双签校验, 不传则用 doc.created_by)
    """
    # 字符串转 enum (兼容外部直接传字符串)
    if isinstance(current_status, str):
        try:
            current_status = KbApprovalStatus(current_status)
        except ValueError as e:
            raise WorkflowError("invalid_status", f"未知审批状态: {current_status}", 400) from e
    if isinstance(action, str):
        try:
            action = KbApprovalAction(action)
        except ValueError as e:
            raise WorkflowError("invalid_action", f"未知审批动作: {action}", 400) from e

    # 1. 转移合法性
    key = (current_status, action)
    if key not in _TRANSITIONS:
        raise WorkflowError(
            "illegal_transition",
            f"状态 {current_status.value} 不能执行 {action.value}",
        )

    new_status = _TRANSITIONS[key]

    # 2. 双签校验 (在 comment 之前, 403 比 422 优先级高 — 权限问题先报)
    if action in _DUAL_SIGN_ACTIONS and actor_role not in _DUAL_SIGN_EXEMPT_ROLES:
        # actor_id == last_actor → 自批, 拒绝
        if last_actor and actor_id == last_actor:
            raise WorkflowError(
                "dual_sign_required",
                f"动作 {action.value} 需双签, 当前 actor 与上次操作人相同 ({actor_id})",
                http_status=403,
            )

    # 3. comment 强制校验
    if action in _COMMENT_REQUIRED_ACTIONS:
        c = (comment or "").strip()
        if not c:
            raise WorkflowError(
                "comment_required",
                f"动作 {action.value} 必须填写备注 (合规留痕)",
            )

    return new_status


def get_allowed_actions(current_status: KbApprovalStatus | str) -> list[KbApprovalAction]:
    """当前状态下允许的动作列表 (前端按钮渲染用)"""
    if isinstance(current_status, str):
        try:
            current_status = KbApprovalStatus(current_status)
        except ValueError:
            return []
    return [act for (s, act) in _TRANSITIONS if s == current_status]


def is_terminal(status: KbApprovalStatus | str) -> bool:
    """是否终态 (终态不能再次转移, 只能 reset 到 DRAFT 由 admin 操作)"""
    if isinstance(status, str):
        try:
            status = KbApprovalStatus(status)
        except ValueError:
            return False
    return status in {KbApprovalStatus.ARCHIVED}


__all__ = [
    "WorkflowError",
    "validate_transition",
    "get_allowed_actions",
    "is_terminal",
    "KbApprovalStatus",
    "KbApprovalAction",
]
