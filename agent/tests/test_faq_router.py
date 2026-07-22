"""FAQ 路由层单元测试

覆盖 faq_router 端点函数的参数校验、错误响应、审批流程调用。
直接调用端点函数，mock app.state 依赖注入。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from smartcs.services.common.faq_router import (
    FaqCreateRequest,
    FaqSearchRequest,
    FaqUpdateRequest,
    create_faq_endpoint,
    delete_faq_endpoint,
    get_faq_endpoint,
    list_faqs_endpoint,
    search_faq_endpoint,
    update_faq_endpoint,
)
from smartcs.shared.exceptions import SmartCSError


def _make_request(app_state: dict | None = None) -> Request:
    """构造带 app.state 的 mock Request"""
    req = MagicMock(spec=Request)
    if app_state is not None:
        req.app.state = MagicMock()
        for k, v in app_state.items():
            setattr(req.app.state, k, v)
    else:
        req.app.state = MagicMock()
    return req


def _make_user(user_id: str = "u1", role: str = "agent") -> MagicMock:
    user = MagicMock()
    user.user_id = user_id
    user.role = role
    return user


class TestFaqEndpoints:
    """FAQ CRUD 端点测试"""

    @pytest.mark.asyncio
    async def test_list_returns_faqs(self) -> None:
        """GET /kb/faq 返回 FAQ 列表"""
        from unittest.mock import patch

        req = _make_request({"db_session_factory": MagicMock()})
        with patch("smartcs.services.common.faq_router.list_faqs", new=AsyncMock(return_value=([], 0))):
            resp = await list_faqs_endpoint(req, category="billing", limit=10, offset=0)
            assert resp["faqs"] == []
            assert resp["total"] == 0

    @pytest.mark.asyncio
    async def test_get_not_found_raises(self) -> None:
        """FAQ 不存在 → 2001"""
        req = _make_request({"db_session_factory": MagicMock()})
        with patch("smartcs.services.common.faq_router.get_faq", new=AsyncMock(return_value=None)):
            with pytest.raises(SmartCSError) as exc:
                await get_faq_endpoint("missing-faq", req)
            assert exc.value.code == 2001

    @pytest.mark.asyncio
    async def test_update_not_found_raises(self) -> None:
        """更新不存在的 FAQ → 2001"""
        req = _make_request({"db_session_factory": MagicMock()})
        user = _make_user()
        with patch("smartcs.services.common.faq_router.update_faq", new=AsyncMock(return_value=False)):
            with pytest.raises(SmartCSError) as exc:
                await update_faq_endpoint("missing", FaqUpdateRequest(question="x"), req, user)
            assert exc.value.code == 2001

    @pytest.mark.asyncio
    async def test_delete_not_found_raises(self) -> None:
        """删除不存在的 FAQ → 2001"""
        req = _make_request({"db_session_factory": MagicMock()})
        user = _make_user()
        with patch("smartcs.services.common.faq_router.delete_faq", new=AsyncMock(return_value=False)):
            with pytest.raises(SmartCSError) as exc:
                await delete_faq_endpoint("missing", req, user)
            assert exc.value.code == 2001

    @pytest.mark.asyncio
    async def test_no_db_raises_5001(self) -> None:
        """DB 未就绪→5001"""
        req = _make_request({"db_session_factory": None})
        with pytest.raises(SmartCSError) as exc:
            await list_faqs_endpoint(req)
        assert exc.value.code == 5001

    @pytest.mark.asyncio
    async def test_create_with_duplicate_returns_409(self) -> None:
        """语义重复返回 409"""
        from unittest.mock import patch

        req = _make_request(
            {
                "db_session_factory": MagicMock(),
                "embedding_provider": MagicMock(),
                "embedding_breaker": MagicMock(),
                "milvus_collection": MagicMock(),
            }
        )
        req.app.state.embedding_breaker.is_available = True
        req.app.state.embedding_breaker.provider = MagicMock()
        user = _make_user()

        with patch(
            "smartcs.services.common.faq_router.check_faq_duplicate",
            new=AsyncMock(return_value=[{"faq_id": "dup", "similarity": 0.95}]),
        ):
            resp = await create_faq_endpoint(
                FaqCreateRequest(question="test", answer="ans", category="billing"), req, user
            )
            assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_create_no_dup_creates_faq(self) -> None:
        """无重复→正常创建"""
        from unittest.mock import MagicMock, patch

        req = _make_request(
            {
                "db_session_factory": MagicMock(),
                "embedding_provider": MagicMock(),
                "milvus_collection": MagicMock(),
            }
        )
        user = _make_user()

        mock_faq = MagicMock()
        mock_faq.id = "new-id"
        mock_faq.approval_status = "DRAFT"

        with (
            patch("smartcs.services.common.faq_router.check_faq_duplicate", new=AsyncMock(return_value=[])),
            patch("smartcs.services.common.faq_router.create_faq", new=AsyncMock(return_value=mock_faq)),
        ):
            resp = await create_faq_endpoint(
                FaqCreateRequest(question="test", answer="ans", category="billing"), req, user
            )
            assert resp["faq_id"] == "new-id"
            assert resp["approval_status"] == "DRAFT"

    @pytest.mark.asyncio
    async def test_search_endpoint_calls_service(self) -> None:
        """POST /kb/faq/search 调用 search_faq"""
        from unittest.mock import patch

        req = _make_request({"db_session_factory": MagicMock(), "redis_client": MagicMock()})
        user = _make_user()

        with patch(
            "smartcs.services.common.faq_router.search_faq",
            new=AsyncMock(return_value={"match_type": "semantic", "results": []}),
        ) as mock_search:
            resp = await search_faq_endpoint(FaqSearchRequest(query="test"), req, user)
            mock_search.assert_awaited_once()
            assert resp["match_type"] == "semantic"
