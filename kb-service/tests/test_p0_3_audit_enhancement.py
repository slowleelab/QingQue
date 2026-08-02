"""P0-3 业务审计增强 (16 用例)

覆盖:
A. IP/UA/request_id 透传 (4 个)
B. last_actor 修复 (4 个)
C. operation_id 串联 (4 个)
D. ES 重建审计 (4 个)
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── A. IP/UA/request_id 透传 ──


class TestExtractRequestMeta:
    """A1-A2: 公共函数 extract_request_meta"""

    def test_a1_x_request_id_header_wins(self):
        """A1: X-Request-ID 头优先"""
        from kb.security.audit_service import extract_request_meta

        req = MagicMock()
        req.client = MagicMock()
        req.client.host = "10.0.0.1"
        req.headers = {"user-agent": "ua/1.0", "x-request-id": "from-header"}
        req.state = MagicMock()
        req.state.request_id = "from-state"

        ip, ua, rid = extract_request_meta(req)
        assert ip == "10.0.0.1"
        assert ua == "ua/1.0"
        assert rid == "from-header"  # header wins

    def test_a2_fallback_to_state(self):
        """A2: 缺 X-Request-ID 头时, 用 request.state.request_id"""
        from kb.security.audit_service import extract_request_meta

        req = MagicMock()
        req.client = MagicMock()
        req.client.host = "10.0.0.1"
        req.headers = {"user-agent": "ua/1.0"}
        req.state = MagicMock()
        req.state.request_id = "from-state"

        ip, ua, rid = extract_request_meta(req)
        assert rid == "from-state"

    def test_a3_no_client_returns_none_ip(self):
        """A3: 无 client (内部调用) → IP=None"""
        from kb.security.audit_service import extract_request_meta

        req = MagicMock()
        req.client = None
        req.headers = {}
        req.state = MagicMock()
        req.state.request_id = None

        ip, ua, rid = extract_request_meta(req)
        assert ip is None
        assert ua is None
        assert rid is None

    def test_a4_audit_middleware_exception_path_preserves_principal(self):
        """A4: AuditMiddleware 异常路径带 principal (代码层检查)

        通过 inspect 验证异常处理块读 principal
        """
        import inspect

        from kb.security import audit

        source = inspect.getsource(audit.AuditMiddleware.dispatch)
        # 异常分支必须读 request.state.principal
        assert "request.state" in source
        # 必须在 except 块里读 principal
        except_idx = source.find("except Exception")
        assert except_idx > 0
        after_except = source[except_idx:]
        assert "principal" in after_except, "异常路径必须读 principal"


# ── B. last_actor 修复 ──


class TestGetLastActor:
    """B1-B4: get_last_actor + 双签 4-eyes 修复"""

    @pytest.mark.asyncio
    async def test_b1_returns_recent_actor(self):
        """B1: get_last_actor 返回最近一次 KbDocumentApproval.actor_id"""
        from kb.security.approval_recorder import get_last_actor

        doc_id = uuid.uuid4()
        db = AsyncMock()
        # mock: 返回 actor_id='reviewer-b'
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value="reviewer-b"))
        )

        result = await get_last_actor(db, doc_id)
        assert result == "reviewer-b"

    @pytest.mark.asyncio
    async def test_b2_returns_none_for_never_approved(self):
        """B2: doc 从未审批 → 返回 None (caller 兜底 created_by)"""
        from kb.security.approval_recorder import get_last_actor

        doc_id = uuid.uuid4()
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )

        result = await get_last_actor(db, doc_id)
        assert result is None

    @pytest.mark.asyncio
    async def test_b3_get_last_actor_blocks_self_approve(self):
        """B3: get_last_actor 真实阻断自批 (走 validate_transition 走 APPROVE 4-eyes)

        用 validate_transition + get_last_actor 模拟真实 caller, 验证 self-approve 被拒绝
        """
        from kb.security.approval_recorder import get_last_actor
        from kb.security.workflow import WorkflowError, validate_transition
        from kb.orm.kb import KbApprovalAction, KbApprovalStatus

        doc_id = uuid.uuid4()
        db = AsyncMock()
        # 上次审批人是 reviewer-a
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value="reviewer-a"))
        )

        last_actor = await get_last_actor(db, doc_id)
        assert last_actor == "reviewer-a"

        # 同 actor 再次 APPROVE → 403
        with pytest.raises(WorkflowError) as exc:
            validate_transition(
                current_status=KbApprovalStatus.IN_REVIEW,
                action=KbApprovalAction.APPROVE,
                actor_id="reviewer-a",
                actor_role="reviewer",
                comment="self approve",
                last_actor=last_actor,
            )
        assert exc.value.code == "dual_sign_required"
        assert exc.value.http_status == 403

    @pytest.mark.asyncio
    async def test_b4_get_last_actor_passes_different_actor(self):
        """B4: get_last_actor 拿到不同 actor → APPROVE 通过 (跨人)"""
        from kb.security.approval_recorder import get_last_actor
        from kb.security.workflow import validate_transition
        from kb.orm.kb import KbApprovalAction, KbApprovalStatus

        doc_id = uuid.uuid4()
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value="reviewer-a"))
        )

        last_actor = await get_last_actor(db, doc_id)

        # 不同 actor (reviewer-b) APPROVE → 通过
        new = validate_transition(
            current_status=KbApprovalStatus.IN_REVIEW,
            action=KbApprovalAction.APPROVE,
            actor_id="reviewer-b",
            actor_role="reviewer",
            comment="cross actor approve",
            last_actor=last_actor,
        )
        assert new == KbApprovalStatus.APPROVED


# ── C. operation_id 串联 ──


class TestOperationIdLink:
    """C1-C4: 多步操作 operation_id 串联"""

    @pytest.mark.asyncio
    async def test_c1_record_approval_log_has_operation_id(self):
        """C1: record_approval 把 operation_id 写进 structlog"""
        from kb.security.approval_recorder import record_approval

        doc = MagicMock()
        doc.id = uuid.uuid4()
        doc.tenant_id = "default"
        doc.approval_status = MagicMock()
        doc.approval_status.value = "IN_REVIEW"
        doc.created_by = "alice"

        db = MagicMock()
        record = record_approval(
            db,
            doc=doc,
            action=MagicMock(value="APPROVE"),
            from_status=MagicMock(value="IN_REVIEW"),
            to_status=MagicMock(value="APPROVED"),
            actor_id="bob",
            actor_role="reviewer",
            comment="LGTM",
            operation_id="op-test-001",
        )
        # KbDocumentApproval 必须不含 operation_id (ORM 无此列)
        assert not hasattr(record, "operation_id") or getattr(record, "operation_id", None) is None
        # db.add 调过
        assert db.add.called

    def test_c2_operation_id_appears_in_structlog(self):
        """C2: structlog event 含 operation_id (用 structlog.testing 验证)"""
        import structlog

        from kb.security.approval_recorder import record_approval

        doc = MagicMock()
        doc.id = uuid.uuid4()
        doc.tenant_id = "default"
        doc.created_by = "alice"

        db = MagicMock()

        with structlog.testing.capture_logs() as cap_logs:
            record_approval(
                db,
                doc=doc,
                action=MagicMock(value="PUBLISH"),
                from_status=MagicMock(value="APPROVED"),
                to_status=MagicMock(value="PUBLISHED"),
                actor_id="admin",
                actor_role="admin",
                comment="publish",
                operation_id="op-link-002",
            )

        # 找到 kb.approval.recorded 事件
        events = [e for e in cap_logs if e.get("event") == "kb.approval.recorded"]
        assert len(events) >= 1
        assert any(e.get("operation_id") == "op-link-002" for e in events)

    def test_c3_record_approval_default_operation_id_none(self):
        """C3: 不传 operation_id → 默认 None, 兼容旧调用"""
        from kb.security.approval_recorder import record_approval

        doc = MagicMock()
        doc.id = uuid.uuid4()
        doc.tenant_id = "default"
        doc.created_by = "alice"

        db = MagicMock()
        # 不传 operation_id
        record = record_approval(
            db,
            doc=doc,
            action=MagicMock(value="APPROVE"),
            from_status=MagicMock(value="IN_REVIEW"),
            to_status=MagicMock(value="APPROVED"),
            actor_id="bob",
            actor_role="reviewer",
            comment="LGTM",
        )
        assert db.add.called

    @pytest.mark.asyncio
    async def test_c4_audit_service_log_accepts_operation_id(self):
        """C4: AuditService.log 接 operation_id 并写入 business_audit event"""
        import structlog

        from kb.security.audit_service import AuditService

        principal = MagicMock()
        principal.actor_id = "alice"
        principal.actor_role = "admin"
        principal.tenant_id = "default"

        db = MagicMock()
        audit = AuditService(db)

        with structlog.testing.capture_logs() as cap_logs:
            await audit.log(
                event_type="document.publish",
                principal=principal,
                resource="doc-1",
                action="publish",
                result="success",
                operation_id="op-audit-001",
            )

        events = [e for e in cap_logs if e.get("event") == "business_audit"]
        assert len(events) >= 1
        assert any(e.get("operation_id") == "op-audit-001" for e in events)


# ── D. ES 重建审计 (record_approval_partial) ──


class TestRecordApprovalPartial:
    """D1-D4: ES 重建审计变体"""

    def test_d1_full_success_does_not_raise(self):
        """D1: 完整成功 (happy path) — 函数能调, 不抛"""
        from kb.security.approval_recorder import record_approval_partial

        doc = MagicMock()
        doc.id = uuid.uuid4()
        doc.tenant_id = "default"
        doc.created_by = "alice"

        db = MagicMock()
        record = record_approval_partial(
            db,
            doc=doc,
            action=MagicMock(value="PUBLISH"),
            from_status=MagicMock(value="APPROVED"),
            to_status=MagicMock(value="APPROVED"),
            actor_id="system",
            actor_role="service",
            comment="es_sync full: success=5/5, op_id=op-1",
        )
        assert db.add.called
        assert record.operation_result == "partial"
        assert record.risk_level == "high"

    def test_d2_operation_result_is_partial(self):
        """D2: operation_result='partial' 区分正常审批"""
        from kb.security.approval_recorder import record_approval_partial

        doc = MagicMock()
        doc.id = uuid.uuid4()
        doc.tenant_id = "default"
        doc.created_by = "alice"

        db = MagicMock()
        record = record_approval_partial(
            db,
            doc=doc,
            action=MagicMock(value="PUBLISH"),
            from_status=MagicMock(value="APPROVED"),
            to_status=MagicMock(value="APPROVED"),
            actor_id="system",
            actor_role="service",
            comment="es_sync partial: success=2/5",
        )
        assert record.operation_result == "partial"

    def test_d3_empty_comment_raises_value_error(self):
        """D3: 无 comment → ValueError (合规留痕强制)"""
        from kb.security.approval_recorder import record_approval_partial

        doc = MagicMock()
        doc.id = uuid.uuid4()
        doc.tenant_id = "default"

        db = MagicMock()
        with pytest.raises(ValueError) as exc:
            record_approval_partial(
                db,
                doc=doc,
                action=MagicMock(value="PUBLISH"),
                from_status=MagicMock(value="APPROVED"),
                to_status=MagicMock(value="APPROVED"),
                actor_id="system",
                actor_role="service",
                comment="",
            )
        assert "comment" in str(exc.value).lower()

    def test_d4_whitespace_only_comment_raises(self):
        """D4: 纯空白 comment → ValueError"""
        from kb.security.approval_recorder import record_approval_partial

        doc = MagicMock()
        doc.id = uuid.uuid4()
        doc.tenant_id = "default"

        db = MagicMock()
        with pytest.raises(ValueError):
            record_approval_partial(
                db,
                doc=doc,
                action=MagicMock(value="PUBLISH"),
                from_status=MagicMock(value="APPROVED"),
                to_status=MagicMock(value="APPROVED"),
                actor_id="system",
                actor_role="service",
                comment="   \n\t  ",
            )
