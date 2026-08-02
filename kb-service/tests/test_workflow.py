"""审批工作流状态机 + 端点测试 (I1-C3)

覆盖:
- 状态机: 7 状态 + 6 动作的合法/非法转移, comment 强制, 双签, 终态
- 端点: 5 动作 + GET 审批历史 (用 FastAPI TestClient + mock DB session)
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

# 这些依赖在 poetry venv 里需要, 缺则整个文件 skip
httpx = pytest.importorskip("httpx")
fastapi = pytest.importorskip("fastapi")

from kb.orm.kb import KbApprovalAction, KbApprovalStatus  # noqa: E402
from kb.security.workflow import (  # noqa: E402
    WorkflowError,
    get_allowed_actions,
    is_terminal,
    validate_transition,
)


# ── 状态机单元测试 ──


class TestStateMachineTransitions:
    """合法转移矩阵"""

    @pytest.mark.parametrize(
        "from_status,action,to_status,comment",
        [
            (KbApprovalStatus.DRAFT, KbApprovalAction.SUBMIT, KbApprovalStatus.IN_REVIEW, None),
            (KbApprovalStatus.IN_REVIEW, KbApprovalAction.APPROVE, KbApprovalStatus.APPROVED, None),
            (KbApprovalStatus.IN_REVIEW, KbApprovalAction.REJECT, KbApprovalStatus.REJECTED, "驳回原因"),
            (KbApprovalStatus.APPROVED, KbApprovalAction.PUBLISH, KbApprovalStatus.PUBLISHED, None),
            (KbApprovalStatus.PUBLISHED, KbApprovalAction.ARCHIVE, KbApprovalStatus.ARCHIVED, "下架原因"),
            (KbApprovalStatus.PUBLISHED, KbApprovalAction.SUPERSEDE, KbApprovalStatus.SUPERSEDED, "替代原因"),
            (KbApprovalStatus.SUPERSEDED, KbApprovalAction.ARCHIVE, KbApprovalStatus.ARCHIVED, "归档原因"),
            (KbApprovalStatus.REJECTED, KbApprovalAction.SUBMIT, KbApprovalStatus.DRAFT, None),
            (KbApprovalStatus.DRAFT, KbApprovalAction.CREATE, KbApprovalStatus.DRAFT, None),
        ],
    )
    def test_legal_transitions(self, from_status, action, to_status, comment):
        new = validate_transition(
            current_status=from_status,
            action=action,
            actor_id="alice",
            actor_role="editor",
            comment=comment,
        )
        assert new == to_status

    @pytest.mark.parametrize(
        "from_status,action",
        [
            (KbApprovalStatus.DRAFT, KbApprovalAction.APPROVE),  # 不能 DRAFT 直批
            (KbApprovalStatus.PUBLISHED, KbApprovalAction.REJECT),  # PUBLISHED 不能 reject
            (KbApprovalStatus.ARCHIVED, KbApprovalAction.SUBMIT),  # 终态不能动
            (KbApprovalStatus.ARCHIVED, KbApprovalAction.PUBLISH),
            (KbApprovalStatus.SUPERSEDED, KbApprovalAction.PUBLISH),  # SUPERSEDED 不能直发
        ],
    )
    def test_illegal_transitions(self, from_status, action):
        with pytest.raises(WorkflowError) as exc:
            validate_transition(
                current_status=from_status,
                action=action,
                actor_id="alice",
                actor_role="editor",
                comment="required comment" if action in {KbApprovalAction.REJECT, KbApprovalAction.ARCHIVE} else None,
            )
        assert exc.value.code == "illegal_transition"


class TestCommentRequired:
    """comment 强制: REJECT/ARCHIVE/SUPERSEDE 必须填"""

    @pytest.mark.parametrize(
        "from_status,action",
        [
            (KbApprovalStatus.IN_REVIEW, KbApprovalAction.REJECT),
            (KbApprovalStatus.PUBLISHED, KbApprovalAction.ARCHIVE),
            (KbApprovalStatus.PUBLISHED, KbApprovalAction.SUPERSEDE),
        ],
    )
    def test_comment_required_missing(self, from_status, action):
        with pytest.raises(WorkflowError) as exc:
            validate_transition(
                current_status=from_status,
                action=action,
                actor_id="alice",
                actor_role="editor",
                comment=None,
            )
        assert exc.value.code == "comment_required"

    def test_comment_required_reject_whitespace(self):
        with pytest.raises(WorkflowError) as exc:
            validate_transition(
                current_status=KbApprovalStatus.IN_REVIEW,
                action=KbApprovalAction.REJECT,
                actor_id="alice",
                actor_role="editor",
                comment="   \n\t  ",
            )
        assert exc.value.code == "comment_required"

    def test_comment_required_fulfilled(self):
        new = validate_transition(
            current_status=KbApprovalStatus.IN_REVIEW,
            action=KbApprovalAction.REJECT,
            actor_id="alice",
            actor_role="reviewer",
            comment="内容不合规, 需补充来源依据",
        )
        assert new == KbApprovalStatus.REJECTED


class TestDualSign:
    """双签 4-eyes: APPROVE 时 actor != last_actor"""

    def test_dual_sign_self_approve_blocked(self):
        with pytest.raises(WorkflowError) as exc:
            validate_transition(
                current_status=KbApprovalStatus.IN_REVIEW,
                action=KbApprovalAction.APPROVE,
                actor_id="alice",
                actor_role="editor",  # 非 admin
                comment=None,
                last_actor="alice",  # 同人自批
            )
        assert exc.value.code == "dual_sign_required"
        assert exc.value.http_status == 403

    def test_dual_sign_passed(self):
        new = validate_transition(
            current_status=KbApprovalStatus.IN_REVIEW,
            action=KbApprovalAction.APPROVE,
            actor_id="bob",
            actor_role="reviewer",
            comment=None,
            last_actor="alice",
        )
        assert new == KbApprovalStatus.APPROVED

    def test_dual_sign_admin_exempt(self):
        """admin 角色可自批"""
        new = validate_transition(
            current_status=KbApprovalStatus.IN_REVIEW,
            action=KbApprovalAction.APPROVE,
            actor_id="alice",
            actor_role="admin",
            comment=None,
            last_actor="alice",
        )
        assert new == KbApprovalStatus.APPROVED

    def test_dual_sign_service_NOT_exempt(self):
        """P0-2.4 BREAKING: service 角色不再豁免双签, 必须 4-eyes

        之前 service 可自批 (机器审批场景), 现收紧到只 admin 人类管理员可自批
        """
        with pytest.raises(WorkflowError) as exc:
            validate_transition(
                current_status=KbApprovalStatus.IN_REVIEW,
                action=KbApprovalAction.APPROVE,
                actor_id="svc-1",
                actor_role="service",
                comment=None,
                last_actor="svc-1",
            )
        assert exc.value.code == "dual_sign_required"
        assert exc.value.http_status == 403


class TestTerminalState:
    """终态 ARCHIVED 不能再次转移"""

    def test_terminal_detect(self):
        assert is_terminal(KbApprovalStatus.ARCHIVED) is True
        assert is_terminal(KbApprovalStatus.PUBLISHED) is False
        assert is_terminal("ARCHIVED") is True
        assert is_terminal("PUBLISHED") is False

    def test_terminal_rejects_all(self):
        for act in KbApprovalAction:
            if act == KbApprovalAction.ARCHIVE:
                # 已经是 ARCHIVED 再 archive 也不合法
                pass
            with pytest.raises(WorkflowError) as exc:
                validate_transition(
                    current_status=KbApprovalStatus.ARCHIVED,
                    action=act,
                    actor_id="alice",
                    actor_role="admin",
                    comment="c" if act in {KbApprovalAction.REJECT, KbApprovalAction.ARCHIVE, KbApprovalAction.SUPERSEDE} else None,
                )
            assert exc.value.code == "illegal_transition"


class TestAllowedActions:
    """前端按钮渲染用"""

    def test_draft_actions(self):
        acts = get_allowed_actions(KbApprovalStatus.DRAFT)
        assert KbApprovalAction.SUBMIT in acts
        assert KbApprovalAction.CREATE in acts

    def test_in_review_actions(self):
        acts = get_allowed_actions(KbApprovalStatus.IN_REVIEW)
        assert KbApprovalAction.APPROVE in acts
        assert KbApprovalAction.REJECT in acts

    def test_published_actions(self):
        acts = get_allowed_actions(KbApprovalStatus.PUBLISHED)
        assert KbApprovalAction.ARCHIVE in acts
        assert KbApprovalAction.SUPERSEDE in acts

    def test_archived_empty(self):
        assert get_allowed_actions(KbApprovalStatus.ARCHIVED) == []


class TestStringInputs:
    """支持字符串入参 (兼容 API 端点)"""

    def test_string_status_and_action(self):
        new = validate_transition(
            current_status="DRAFT",
            action="SUBMIT",
            actor_id="alice",
            actor_role="editor",
            comment=None,
        )
        assert new == KbApprovalStatus.IN_REVIEW

    def test_invalid_status_string(self):
        with pytest.raises(WorkflowError) as exc:
            validate_transition(
                current_status="UNKNOWN",
                action="SUBMIT",
                actor_id="alice",
                actor_role="editor",
                comment=None,
            )
        assert exc.value.code == "invalid_status"

    def test_invalid_action_string(self):
        with pytest.raises(WorkflowError) as exc:
            validate_transition(
                current_status="DRAFT",
                action="NUKE",
                actor_id="alice",
                actor_role="editor",
                comment=None,
            )
        assert exc.value.code == "invalid_action"


# ── 端点集成测试 (mock DB) ──


def _make_mock_doc(**overrides) -> SimpleNamespace:
    """构造 KbDocument mock, 不走真实 DB"""
    base = dict(
        id="00000000-0000-0000-0000-000000000001",
        approval_status=KbApprovalStatus.DRAFT,
        is_deleted=False,
        tenant_id="default",
        created_by="alice",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestApprovalEndpointLogic:
    """端点逻辑: 不启 FastAPI app, 直接调 _execute_action 用 mock db/principal"""

    @pytest.mark.asyncio
    async def test_submit_draft_to_in_review(self):
        from kb.api.approval import _execute_action, WorkflowActionRequest

        doc = _make_mock_doc(approval_status=KbApprovalStatus.DRAFT, created_by="alice")
        db = AsyncMock()
        db.get = AsyncMock(return_value=doc)
        db.add = MagicMock()
        db.commit = AsyncMock()

        principal = SimpleNamespace(actor_id="alice", actor_role="editor", roles=["editor"])
        request = MagicMock()
        request.client.host = "127.0.0.1"
        request.headers = {"user-agent": "test/1.0", "x-request-id": "req-1"}
        request.state.request_id = "req-1"

        resp = await _execute_action(
            "00000000-0000-0000-0000-000000000001",
            KbApprovalAction.SUBMIT,
            WorkflowActionRequest(comment=None),
            db, principal, request,
        )
        assert resp.action == "SUBMIT"
        assert resp.from_status == "DRAFT"
        assert resp.to_status == "IN_REVIEW"
        assert doc.approval_status == KbApprovalStatus.IN_REVIEW
        assert db.add.call_count == 1  # 1 条 KbDocumentApproval 记录

    @pytest.mark.asyncio
    async def test_approve_requires_dual_sign(self):
        """actor == created_by → 403"""
        from kb.api.approval import _execute_action, WorkflowActionRequest
        from fastapi import HTTPException

        doc = _make_mock_doc(approval_status=KbApprovalStatus.IN_REVIEW, created_by="alice")
        db = AsyncMock()
        db.get = AsyncMock(return_value=doc)

        principal = SimpleNamespace(actor_id="alice", actor_role="editor", roles=["editor"])
        request = MagicMock()
        request.client.host = "127.0.0.1"
        request.headers = {"user-agent": "test"}
        request.state.request_id = None

        with pytest.raises(HTTPException) as exc:
            await _execute_action(
                "00000000-0000-0000-0000-000000000001",
                KbApprovalAction.APPROVE,
                WorkflowActionRequest(comment=None),
                db, principal, request,
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_reject_requires_comment(self):
        from kb.api.approval import _execute_action, WorkflowActionRequest
        from fastapi import HTTPException

        doc = _make_mock_doc(approval_status=KbApprovalStatus.IN_REVIEW, created_by="alice")
        db = AsyncMock()
        db.get = AsyncMock(return_value=doc)

        principal = SimpleNamespace(actor_id="bob", actor_role="reviewer", roles=["reviewer"])
        request = MagicMock()
        request.client.host = "127.0.0.1"
        request.headers = {}
        request.state.request_id = None

        with pytest.raises(HTTPException) as exc:
            await _execute_action(
                "00000000-0000-0000-0000-000000000001",
                KbApprovalAction.REJECT,
                WorkflowActionRequest(comment=None),  # 缺 comment
                db, principal, request,
            )
        assert exc.value.status_code == 422
        assert "备注" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_archive_archived_rejected(self):
        """终态文档被 _execute_action 拒绝"""
        from kb.api.approval import _execute_action, WorkflowActionRequest
        from fastapi import HTTPException

        doc = _make_mock_doc(approval_status=KbApprovalStatus.ARCHIVED, created_by="admin")
        db = AsyncMock()
        db.get = AsyncMock(return_value=doc)

        principal = SimpleNamespace(actor_id="admin", actor_role="admin", roles=["admin"])
        request = MagicMock()
        request.client.host = "127.0.0.1"
        request.headers = {}
        request.state.request_id = None

        with pytest.raises(HTTPException) as exc:
            await _execute_action(
                "00000000-0000-0000-0000-000000000001",
                KbApprovalAction.ARCHIVE,
                WorkflowActionRequest(comment="重复下架"),
                db, principal, request,
            )
        assert exc.value.status_code == 422
        assert "终态" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_archive_records_high_risk(self):
        from kb.api.approval import _execute_action, WorkflowActionRequest

        doc = _make_mock_doc(approval_status=KbApprovalStatus.PUBLISHED, created_by="admin")
        db = AsyncMock()
        db.get = AsyncMock(return_value=doc)
        db.add = MagicMock()
        db.commit = AsyncMock()

        principal = SimpleNamespace(actor_id="admin", actor_role="admin", roles=["admin"])
        request = MagicMock()
        request.client.host = "127.0.0.1"
        request.headers = {"user-agent": "ops/2.0", "x-request-id": "r-2"}
        request.state.request_id = "r-2"

        await _execute_action(
            "00000000-0000-0000-0000-000000000001",
            KbApprovalAction.ARCHIVE,
            WorkflowActionRequest(comment="合规紧急下架"),
            db, principal, request,
        )
        # db.add 调 1 次, 入参是 KbDocumentApproval, risk_level 必须是 high
        record = db.add.call_args[0][0]
        assert record.risk_level == "high"
        assert record.action == KbApprovalAction.ARCHIVE
        assert record.tenant_id == "default"
        assert record.ip == "127.0.0.1"
        assert record.ua == "ops/2.0"
        assert record.request_id == "r-2"
        # 5 年留存
        assert record.retention_until is not None

    @pytest.mark.asyncio
    async def test_invalid_doc_id_400(self):
        from kb.api.approval import _execute_action, WorkflowActionRequest
        from fastapi import HTTPException

        db = AsyncMock()
        principal = SimpleNamespace(actor_id="x", actor_role="x", roles=[])
        request = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await _execute_action(
                "not-a-uuid",
                KbApprovalAction.SUBMIT,
                WorkflowActionRequest(comment=None),
                db, principal, request,
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_doc_not_found_404(self):
        from kb.api.approval import _execute_action, WorkflowActionRequest
        from fastapi import HTTPException

        db = AsyncMock()
        db.get = AsyncMock(return_value=None)
        principal = SimpleNamespace(actor_id="x", actor_role="x", roles=[])
        request = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await _execute_action(
                "00000000-0000-0000-0000-000000000099",
                KbApprovalAction.SUBMIT,
                WorkflowActionRequest(comment=None),
                db, principal, request,
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_deleted_doc_410(self):
        from kb.api.approval import _execute_action, WorkflowActionRequest
        from fastapi import HTTPException

        doc = _make_mock_doc(is_deleted=True)
        db = AsyncMock()
        db.get = AsyncMock(return_value=doc)

        principal = SimpleNamespace(actor_id="x", actor_role="x", roles=[])
        request = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await _execute_action(
                "00000000-0000-0000-0000-000000000001",
                KbApprovalAction.SUBMIT,
                WorkflowActionRequest(comment=None),
                db, principal, request,
            )
        assert exc.value.status_code == 410
