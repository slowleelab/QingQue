"""P0-2.4 收紧双签豁免 (BREAKING — 移除 service) (8 用例)

覆盖:
- T28 service 角色自批 → 403 dual_sign_required (BREAKING)
- T29 service 角色跨人审批 → 通过 (4-eyes 满足)
- T30 admin 角色自批 → 通过 (admin 仍豁免)
- T31 admin 角色跨人审批 → 通过
- T32 reviewer 角色自批 → 403
- T33 editor 角色自批 → 403
- T34 service 角色 reject 仍 OK (reject 不是双签动作)
- T35 service 角色 archive 仍需 comment (comment_required 仍生效)
"""

from __future__ import annotations

import pytest

from kb.orm.kb import KbApprovalAction, KbApprovalStatus
from kb.security.workflow import WorkflowError, validate_transition


def _check(action=KbApprovalAction.APPROVE, *, actor_id, actor_role, last_actor, comment=None):
    """便捷调用: 期望成功时返回新状态, 期望失败时抛 WorkflowError"""
    return validate_transition(
        current_status=KbApprovalStatus.IN_REVIEW,
        action=action,
        actor_id=actor_id,
        actor_role=actor_role,
        comment=comment,
        last_actor=last_actor,
    )


# ═══════════════════════════════════════════════════════════════════════
# T28-T33 角色 × 自批/跨人 矩阵
# ═══════════════════════════════════════════════════════════════════════


class TestDualSignRoleMatrix:
    @pytest.mark.parametrize("actor_role", ["editor", "reviewer"])
    def test_non_admin_self_approve_blocked(self, actor_role: str):
        """非 admin 角色自批 → 403 (原语义保持)"""
        with pytest.raises(WorkflowError) as exc:
            _check(actor_id="alice", actor_role=actor_role, last_actor="alice")
        assert exc.value.code == "dual_sign_required"
        assert exc.value.http_status == 403

    def test_service_self_approve_BLOCKED_breaking(self):
        """T28 (BREAKING): service 角色自批 → 403, 不再豁免

        之前 service 可自批 (机器审批场景), 现收紧到只 admin 可自批
        """
        with pytest.raises(WorkflowError) as exc:
            _check(actor_id="svc-1", actor_role="service", last_actor="svc-1")
        assert exc.value.code == "dual_sign_required"
        assert exc.value.http_status == 403

    def test_service_cross_actor_approve_passes(self):
        """T29 service 跨人审批 → 通过 (4-eyes 满足)"""
        new = _check(actor_id="svc-1", actor_role="service", last_actor="alice")
        assert new == KbApprovalStatus.APPROVED

    def test_admin_self_approve_exempt(self):
        """T30 admin 自批 → 通过 (admin 仍豁免)"""
        new = _check(actor_id="alice", actor_role="admin", last_actor="alice")
        assert new == KbApprovalStatus.APPROVED

    def test_admin_cross_actor_approve_passes(self):
        """T31 admin 跨人审批 → 通过"""
        new = _check(actor_id="alice", actor_role="admin", last_actor="bob")
        assert new == KbApprovalStatus.APPROVED

    def test_reviewer_self_approve_blocked(self):
        """T32 reviewer 自批 → 403"""
        with pytest.raises(WorkflowError) as exc:
            _check(actor_id="alice", actor_role="reviewer", last_actor="alice")
        assert exc.value.code == "dual_sign_required"

    def test_editor_self_approve_blocked(self):
        """T33 editor 自批 → 403"""
        with pytest.raises(WorkflowError) as exc:
            _check(actor_id="alice", actor_role="editor", last_actor="alice")
        assert exc.value.code == "dual_sign_required"


# ═══════════════════════════════════════════════════════════════════════
# T34-T35 双签豁免移除不影响其他校验
# ═══════════════════════════════════════════════════════════════════════


class TestOtherRulesStillApply:
    def test_service_reject_no_dual_sign(self):
        """T34 service reject 不受双签影响 (reject 不在 _DUAL_SIGN_ACTIONS)

        reject 走 comment_required 校验
        """
        # reject 也允许非 admin 自批 (self-reject 不是双签动作)
        new = _check(
            action=KbApprovalAction.REJECT,
            actor_id="svc-1",
            actor_role="service",
            last_actor="svc-1",
            comment="service reject ok",
        )
        assert new == KbApprovalStatus.REJECTED

    def test_service_archive_comment_required(self):
        """T35 service archive 仍需 comment (comment_required 仍生效)

        service 可 archive 自己的 doc (archive 不是双签动作), 但必须填备注
        ARCHIVE 仅允许从 PUBLISHED/SUPERSEDED 转移
        """
        # 无 comment → comment_required 错
        with pytest.raises(WorkflowError) as exc:
            validate_transition(
                current_status=KbApprovalStatus.PUBLISHED,
                action=KbApprovalAction.ARCHIVE,
                actor_id="svc-1",
                actor_role="service",
                comment=None,
                last_actor="svc-1",
            )
        assert exc.value.code == "comment_required"

        # 有 comment → 通过
        new = validate_transition(
            current_status=KbApprovalStatus.PUBLISHED,
            action=KbApprovalAction.ARCHIVE,
            actor_id="svc-1",
            actor_role="service",
            comment="service archive ok",
            last_actor="svc-1",
        )
        assert new == KbApprovalStatus.ARCHIVED


# ═══════════════════════════════════════════════════════════════════════
# _DUAL_SIGN_EXEMPT_ROLES 常量语义锁定 (代码层回归保护)
# ═══════════════════════════════════════════════════════════════════════


class TestExemptRolesConstant:
    def test_exempt_roles_only_admin(self):
        """代码层: _DUAL_SIGN_EXEMPT_ROLES 必须只含 admin, 不能含 service

        防止以后有人重新加回 service 豁免
        """
        from kb.security.workflow import _DUAL_SIGN_EXEMPT_ROLES

        assert "admin" in _DUAL_SIGN_EXEMPT_ROLES
        assert "service" not in _DUAL_SIGN_EXEMPT_ROLES, (
            "P0-2.4 BREAKING: service 角色不应再豁免双签, 否则审计洞"
        )

    def test_service_role_count_one(self):
        """豁免集合大小为 1 (只 admin)"""
        from kb.security.workflow import _DUAL_SIGN_EXEMPT_ROLES

        assert len(_DUAL_SIGN_EXEMPT_ROLES) == 1
