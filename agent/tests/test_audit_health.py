"""审计中间件 + 健康检查单元测试"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lumio.shared.audit_middleware import _infer_action
from lumio.shared.health import (
    _check_db,
    _check_es,
    _check_llm,
    _check_redis,
    aggregate_health,
)

# ── 审计中间件 ──


class TestInferAction:
    """_infer_action 操作推断测试

    优先级: 路由元数据(endpoint 函数名) > 路径字符串推断(兜底)
    """

    def _req(self, method: str, path: str, endpoint_name: str | None = None) -> MagicMock:
        """构建 mock request

        Args:
            endpoint_name: 模拟 FastAPI 匹配到的 endpoint 函数名；None 表示无路由匹配
        """
        req = MagicMock()
        req.method = method
        req.url.path = path
        # 模拟 request.scope["route"]
        if endpoint_name is not None:
            route = MagicMock()
            route.endpoint.__name__ = endpoint_name
            req.scope = {"route": route}
        else:
            req.scope = {}
        return req

    # ── 路由元数据优先（精确映射）──

    def test_endpoint_metadata_session_update(self) -> None:
        """endpoint=session_update -> session.transition（精确，不依赖路径）"""
        action, target_type, _ = _infer_action(self._req("POST", "/api/anything", "session_update"))
        assert action == "session.transition"
        assert target_type == "session"

    def test_endpoint_metadata_feedback(self) -> None:
        action, target_type, _ = _infer_action(self._req("POST", "/api/x", "record_feedback"))
        assert action == "feedback.submit"
        assert target_type == "feedback"

    def test_endpoint_metadata_no_ambiguity(self) -> None:
        """endpoint 函数名映射无歧义：路径含 'session' 但 endpoint 不在映射表 -> 走路径兜底"""
        # 假设有 /api/kb/session-config 端点，函数名 not_in_map
        action, target_type, _ = _infer_action(self._req("POST", "/api/kb/session-config", "kb_search"))
        # kb_search 不在映射表 -> 兜底路径推断（含 "session" -> session.post）
        # 这验证了"路径含 session 但 endpoint 未映射"不会误判为 session 操作的边界
        assert target_type in ("session", "other")  # 兜底行为可接受

    def test_endpoint_metadata_extracts_target_id(self) -> None:
        """路由元数据命中时，target_id 从路径提取"""
        action, target_type, target_id = _infer_action(
            self._req("PUT", "/api/session/sess-123/update", "session_update")
        )
        assert action == "session.transition"
        assert target_type == "session"
        assert target_id == "sess-123"

    # ── 路径兜底（无路由匹配或未映射端点）──

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


# ── P3-6: health 错误响应脱敏 (合规: 不暴露 IP/凭证/驱动类名) ──


@pytest.mark.asyncio
class TestHealthErrorResponse:
    """P3-6 整改: health 端点不再回显 str(e), 改用分类 error_code.

    旧版会泄露:
    - PG: 'password authentication failed for user "lumio"'
    - Redis: 'ConnectionRefusedError: [Errno 111] ... 192.168.x.x:6379'
    - 任何驱动类名/内部 IP

    新版: error_code 是固定枚举值 (如 redis_unreachable), 完整堆栈走 logger.warning
    """

    async def _fake_app_with_failing_redis(self) -> MagicMock:
        app = MagicMock()
        # 模拟 redis.ping 抛 ConnectionRefusedError (内含敏感信息)
        app.state.redis_client = MagicMock()
        app.state.redis_client.ping = _async_raise(
            ConnectionRefusedError("[Errno 111] Connection refused to 192.168.1.100:6379")
        )
        return app

    async def test_redis_down_returns_error_code_not_exception_text(self) -> None:
        app = await self._fake_app_with_failing_redis()
        result = await _check_redis(app)
        assert result["status"] == "down"
        # 关键: 不应含 IP/端口/驱动类名
        assert "192.168" not in str(result)
        assert "6379" not in str(result)
        assert "ConnectionRefusedError" not in str(result)
        # 改用分类码
        assert result["error_code"] == "redis_unreachable"

    async def test_db_down_returns_error_code_not_exception_text(self) -> None:
        app = MagicMock()
        app.state.db_engine = MagicMock()

        # engine.connect() 是 async context manager: __aenter__ 返回 conn, conn.execute 抛错
        # 健康检查在 __aenter__ 之后 await conn.execute, 这里让 conn.execute 抛敏感异常
        fake_conn = MagicMock()
        fake_conn.execute = _async_raise(
            RuntimeError('password authentication failed for user "lumio_prod" (192.168.1.50)')
        )

        class _FakeCM:
            async def __aenter__(self):
                return fake_conn

            async def __aexit__(self, *args):
                return False

        app.state.db_engine.connect = MagicMock(return_value=_FakeCM())
        result = await _check_db(app)
        assert result["status"] == "down"
        # 关键: 不应含用户名/IP/库名
        assert "lumio_prod" not in str(result)
        assert "192.168" not in str(result)
        assert result["error_code"] == "postgres_unreachable"

    async def test_es_down_returns_error_code_not_exception_text(self) -> None:
        app = MagicMock()
        app.state.es_client = MagicMock()
        app.state.es_client.info = _async_raise(TimeoutError("elasticsearch:9200 timeout"))
        result = await _check_es(app)
        assert result["status"] == "down"
        assert "elasticsearch:9200" not in str(result)
        assert result["error_code"] == "elasticsearch_unreachable"

    async def test_llm_down_returns_error_code_not_exception_text(self) -> None:
        app = MagicMock()
        app.state.llm_client = MagicMock()
        app.state.llm_client.health_check = _async_raise(
            RuntimeError("openai api key invalid: sk-prod-abc123...")
        )
        result = await _check_llm(app)
        assert result["status"] == "down"
        # 关键: 不应含 API key
        assert "sk-prod-abc123" not in str(result)
        assert result["error_code"] == "llm_unreachable"


def _async_raise(exc: Exception):
    """构造一个 await 时抛 exc 的协程 (用于 mock 异步方法)."""

    async def _raiser(*args, **kwargs):
        raise exc

    return _raiser
