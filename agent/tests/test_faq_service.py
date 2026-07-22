"""FAQ 知识库服务单元测试

覆盖 faq_service 的纯逻辑层：查询归一化、缓存 key、审批状态机、
语义去重、三级检索（精确/语义/miss）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from smartcs.services.common.faq_service import (
    _FAQ_TRANSITIONS,
    _cache_key,
    _normalize_query,
    check_faq_duplicate,
    expire_overdue_faqs,
    search_faq,
)


class TestNormalizeQuery:
    """查询归一化测试"""

    def test_lowercase(self) -> None:
        assert _normalize_query("HELLO") == "hello"

    def test_trim_and_collapse_spaces(self) -> None:
        assert _normalize_query("  你好   世界  ") == "你好 世界"

    def test_nfkc_fullwidth_to_ascii(self) -> None:
        """全角字符→半角（NFKC）"""
        result = _normalize_query("ＡＢＣ１２３")
        assert result == "abc123"

    def test_chinese_unchanged(self) -> None:
        assert _normalize_query("信用卡年费怎么减免") == "信用卡年费怎么减免"


class TestCacheKey:
    """精确匹配缓存 key 测试"""

    def test_normalized_produces_deterministic_key(self) -> None:
        assert _cache_key(" 你好 ") == _cache_key("你好")

    def test_different_queries_different_keys(self) -> None:
        assert _cache_key("年费") != _cache_key("额度")

    def test_key_has_prefix(self) -> None:
        assert _cache_key("test").startswith("smartcs:faq:exact:")


class TestApprovalTransitions:
    """审批状态机测试"""

    def test_draft_to_review(self) -> None:
        assert "IN_REVIEW" in _FAQ_TRANSITIONS["DRAFT"]

    def test_review_to_approved_or_rejected(self) -> None:
        assert _FAQ_TRANSITIONS["IN_REVIEW"] == {"APPROVED", "REJECTED"}

    def test_approved_to_published(self) -> None:
        assert "PUBLISHED" in _FAQ_TRANSITIONS["APPROVED"]

    def test_rejected_back_to_draft(self) -> None:
        assert "DRAFT" in _FAQ_TRANSITIONS["REJECTED"]

    def test_published_to_superseded_or_archived(self) -> None:
        assert _FAQ_TRANSITIONS["PUBLISHED"] == {"SUPERSEDED", "ARCHIVED"}

    def test_superseded_to_archived(self) -> None:
        assert "ARCHIVED" in _FAQ_TRANSITIONS["SUPERSEDED"]

    def test_illegal_jump_not_allowed(self) -> None:
        """不能从 DRAFT 直接跳到 PUBLISHED"""
        assert "PUBLISHED" not in _FAQ_TRANSITIONS["DRAFT"]


class TestDuplicateCheck:
    """语义去重检测测试"""

    @pytest.mark.asyncio
    async def test_no_providers_returns_empty(self) -> None:
        """无 embedding/milvus 时返回空列表"""
        result = await check_faq_duplicate("测试", None, None)
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_graceful(self) -> None:
        """检索异常时吞掉返回空"""
        embedding = MagicMock()
        embedding.embed_query = AsyncMock(side_effect=RuntimeError("down"))
        collection = MagicMock()

        result = await check_faq_duplicate("测试", embedding, collection)
        assert result == []

    @pytest.mark.asyncio
    async def test_match_above_threshold_returns_duplicates(self) -> None:
        """相似度≥阈值视为重复"""
        embedding = MagicMock()
        embedding.embed_query = AsyncMock(return_value=[0.1] * 768)
        collection = MagicMock()
        collection.search.return_value = [
            [
                MagicMock(score=0.95, entity={"chunk_id": "faq-1", "content": "重复问题", "category": "billing"}),
                MagicMock(score=0.91, entity={"chunk_id": "faq-2", "content": "另一重复", "category": "billing"}),
            ]
        ]

        result = await check_faq_duplicate("测试", embedding, collection, threshold=0.92)
        assert len(result) == 1  # 0.91 < 0.92 应被过滤
        assert result[0]["faq_id"] == "faq-1"

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self) -> None:
        """所有命中低于阈值"""
        embedding = MagicMock()
        embedding.embed_query = AsyncMock(return_value=[0.1] * 768)
        collection = MagicMock()
        collection.search.return_value = [
            [
                MagicMock(score=0.6, entity={"chunk_id": "faq-3", "content": "不重复"}),
            ]
        ]

        result = await check_faq_duplicate("测试", embedding, collection)
        assert result == []


class TestSearchFaq:
    """FAQ 三级检索测试"""

    @pytest.mark.asyncio
    async def test_exact_match_hit(self) -> None:
        """Redis 缓存命中直接返回 exact"""
        import json

        faq_data = {"id": "faq-1", "question": "年费", "answer": "减免方法..."}
        redis = MagicMock()
        redis.get = AsyncMock(return_value=json.dumps(faq_data).encode())

        result = await search_faq("年费怎么减", redis, session_factory=None)
        assert result["match_type"] == "exact"
        assert result["results"][0]["id"] == "faq-1"

    @pytest.mark.asyncio
    async def test_exact_match_role_filtered_falls_back(self) -> None:
        """精确命中但角色无权限→降级到语义"""
        import json

        faq_data = {"id": "faq-1", "answer": "xxx", "allowed_roles": ["admin"], "card_types": []}
        redis = MagicMock()
        redis.get = AsyncMock(return_value=json.dumps(faq_data).encode())

        result = await search_faq("test", redis, user_role="agent", session_factory=None)
        # 非 admin 角色应被过滤，无 embedding 则降级到 miss
        assert result["match_type"] == "miss"

    @pytest.mark.asyncio
    async def test_semantic_match(self) -> None:
        """Milvus 语义检索返回结果"""
        embedding = MagicMock()
        embedding.embed_query = AsyncMock(return_value=[0.1] * 768)
        collection = MagicMock()
        collection.search.return_value = [
            [
                MagicMock(
                    score=0.88,
                    entity={
                        "chunk_id": "faq-10",
                        "content": "账单日是什么",
                        "category": "billing",
                        "card_types": "",
                    },
                ),
            ]
        ]

        result = await search_faq(
            "账单日",
            None,
            embedding,
            collection,
            session_factory=None,
        )
        assert result["match_type"] == "semantic"
        assert result["results"][0]["faq_id"] == "faq-10"

    @pytest.mark.asyncio
    async def test_no_redis_or_milvus_returns_miss(self) -> None:
        """无 Redis 也无 Milvus→miss"""
        result = await search_faq("任意问题", None, session_factory=None)
        assert result["match_type"] == "miss"
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_semantic_exception_falls_to_miss(self) -> None:
        """语义检索异常→miss（不抛）"""
        embedding = MagicMock()
        embedding.embed_query = AsyncMock(return_value=[0.1] * 768)
        collection = MagicMock()
        collection.search.side_effect = RuntimeError("down")

        result = await search_faq("测试", None, embedding, collection, session_factory=None)
        assert result["match_type"] == "miss"


class TestExpireOverdue:
    """FAQ 自动过期测试"""

    @pytest.mark.asyncio
    async def test_expire_published_past_expiry(self) -> None:
        """已过期 PUBLISHED FAQ 自动下线"""
        expired_faq = MagicMock()
        expired_faq.approval_status = "PUBLISHED"
        expired_faq.is_current_version = True
        expired_faq.is_deleted = False

        # 构造符合 async_sessionmaker 协议的假 session 工厂
        result = MagicMock()
        result.scalars.return_value.all.return_value = [expired_faq]

        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def execute(self, *_a, **_kw):
                return result

            async def commit(self):
                pass

        sf = MagicMock(return_value=_FakeSession())

        count = await expire_overdue_faqs(sf)
        assert expired_faq.approval_status == "SUPERSEDED"
        assert expired_faq.is_current_version is False
        assert count == 1
