"""审计中间件 + 健康检查单元测试"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from smartcs.shared.audit_middleware import _infer_action
from smartcs.shared.health import aggregate_health


# ── 审计中间件 ──


class TestInferAction:
    """_infer_action 路径→操作映射测试"""

    def _req(self, method: str, path: str) -> MagicMock:
        req = MagicMock()
        req.method = method
        req.url.path = path
        return req

    def test_session_update(self) -> None:
        action, target_type, target_id = _infer_action(self._req("POST", "/api/session/update"))
        assert action == "session.transition"
        assert target_type == "session"

    def test_feedback_submit(self) -> None:
        action, target_type, target_id = _infer_action(self._req("POST", "/api/feedback"))
        assert action == "feedback.submit"
        assert target_type == "feedback"

    def test_feedback_undo(self) -> None:
        action, _, _ = _infer_action(self._req("POST", "/api/feedback/undo"))
        assert action == "feedback.undo"

    def test_document_upload(self) -> None:
        action, target_type, _ = _infer_action(self._req("POST", "/api/kb/documents"))
        assert action == "document.upload"
        assert target_type == "document"

    def test_session_hold(self) -> None:
        action, _, _ = _infer_action(self._req("POST", "/api/hold"))
        assert action == "session.hold"

    def test_session_resume(self) -> None:
        action, _, _ = _infer_action(self._req("POST", "/api/resume"))
        assert action == "session.resume"

    def test_review_submit(self) -> None:
        action, target_type, _ = _infer_action(self._req("POST", "/api/review/generate"))
        assert action == "review.post"
        assert target_type == "review"

    def test_notify_receive(self) -> None:
        action, target_type, _ = _infer_action(self._req("POST", "/api/notify"))
        assert action == "notify.receive"
        assert target_type == "notify"

    def test_analyze_request(self) -> None:
        action, _, _ = _infer_action(self._req("POST", "/api/analyze"))
        assert action == "analyze.request"

    def test_unknown_path_falls_back(self) -> None:
        action, target_type, _ = _infer_action(self._req("GET", "/api/unknown/endpoint"))
        assert target_type == "other"
        assert "endpoint" in action

    def test_deep_path_with_session_id(self) -> None:
        """含 session_id 的路径应正确提取"""
        action, target_type, target_id = _infer_action(
            self._req("PUT", "/api/session/sess-123/update")
        )
        assert target_type == "session"
        assert target_id == "sess-123"


# ── 健康检查 ──


class TestAggregateHealth:
    """aggregate_health 结果聚合测试"""

    def test_all_healthy(self) -> None:
        deps = {
            "postgres": {"status": "up"},
            "redis": {"status": "up"},
            "elasticsearch": {"status": "up"},
        }
        status, code = aggregate_health(deps)
        assert status == "healthy"
        assert code == 200

    def test_non_core_down_degraded(self) -> None:
        """非核心依赖 down → degraded, 200"""
        deps = {
            "postgres": {"status": "up"},
            "redis": {"status": "up"},
            "elasticsearch": {"status": "down", "error": "timeout"},
        }
        status, code = aggregate_health(deps)
        assert status == "degraded"
        assert code == 200

    def test_core_down_unhealthy(self) -> None:
        """核心依赖(redis) down → unhealthy, 503"""
        deps = {
            "postgres": {"status": "up"},
            "redis": {"status": "down"},
        }
        status, code = aggregate_health(deps)
        assert status == "unhealthy"
        assert code == 503

    def test_all_skip_is_healthy(self) -> None:
        deps = {
            "postgres": {"status": "skip"},
            "redis": {"status": "skip"},
        }
        status, code = aggregate_health(deps)
        assert status == "healthy"
        assert code == 200

    def test_degraded_with_down_non_core(self) -> None:
        deps = {
            "postgres": {"status": "up"},
            "redis": {"status": "up"},
            "elasticsearch": {"status": "down"},
            "minio": {"status": "down"},
        }
        status, code = aggregate_health(deps)
        assert status == "degraded"
        assert code == 200
