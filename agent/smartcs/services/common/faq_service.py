"""FAQ 知识库服务

提供 FAQ 的 CRUD、审批、索引和检索能力。

检索策略（串行短路）:
1. 精确匹配（Redis）— variant_questions 归一化后精确命中 → 直接返回
2. 语义匹配（Milvus）— question + variants 的 embedding COSINE 相似 → top_k
3. 降级到通用 RAG — 文档检索（调用方处理）
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from datetime import date
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from smartcs.shared.orm_models import KbFaq, KbFaqSearchLog

logger = logging.getLogger(__name__)

_FAQ_CACHE_PREFIX = "smartcs:faq:exact"
_FAQ_CACHE_TTL = 3600  # 1 小时


def _normalize_query(text: str) -> str:
    """查询归一化: NFKC + 小写 + 去首尾空格 + 压缩中间空格"""
    text = unicodedata.normalize("NFKC", text).lower().strip()
    return re.sub(r"\s+", " ", text)


def _cache_key(query: str) -> str:
    """生成 Redis 精确匹配缓存 key"""
    normalized = _normalize_query(query)
    h = hashlib.md5(normalized.encode()).hexdigest()
    return f"{_FAQ_CACHE_PREFIX}:{h}"


# ── CRUD ──


async def create_faq(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    question: str,
    answer: str,
    variant_questions: list[str] | None = None,
    category: str,
    card_types: list[str] | None = None,
    customer_tiers: list[str] | None = None,
    keywords: list[str] | None = None,
    effective_date: date | None = None,
    expiry_date: date | None = None,
    allowed_roles: list[str] | None = None,
    regulatory_tags: list[str] | None = None,
    created_by: str = "system",
) -> KbFaq:
    """创建 FAQ"""
    import uuid_utils

    doc_group = f"faq_{uuid_utils.uuid7().hex[:16]}"

    async with session_factory() as session:
        faq = KbFaq(
            question=question,
            answer=answer,
            variant_questions=variant_questions or [],
            category=category,
            card_types=card_types or [],
            customer_tiers=customer_tiers or [],
            keywords=keywords or [],
            doc_group=doc_group,
            approval_status="DRAFT",
            effective_date=effective_date,
            expiry_date=expiry_date,
            allowed_roles=allowed_roles or [],
            regulatory_tags=regulatory_tags or [],
            created_by=created_by,
        )
        session.add(faq)
        await session.commit()
        await session.refresh(faq)
        return faq


async def list_faqs(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    category: str | None = None,
    approval_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """列出 FAQ"""
    async with session_factory() as session:
        query = select(KbFaq).where(KbFaq.is_deleted == False)  # noqa: E712
        if category:
            query = query.where(KbFaq.category == category)
        if approval_status:
            query = query.where(KbFaq.approval_status == approval_status)

        count_q = select(func.count()).select_from(query.subquery())
        total = (await session.execute(count_q)).scalar() or 0

        query = query.order_by(KbFaq.sort_order, KbFaq.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(query)
        faqs = result.scalars().all()

    return [
        {
            "id": str(f.id),
            "question": f.question,
            "category": f.category,
            "approval_status": f.approval_status,
            "version": f.version,
            "is_current_version": f.is_current_version,
            "card_types": f.card_types,
            "effective_date": f.effective_date.isoformat() if f.effective_date else None,
            "expiry_date": f.expiry_date.isoformat() if f.expiry_date else None,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in faqs
    ], total


async def get_faq(
    session_factory: async_sessionmaker[AsyncSession],
    faq_id: str,
) -> dict | None:
    """获取 FAQ 详情"""
    async with session_factory() as session:
        result = await session.execute(select(KbFaq).where(KbFaq.id == faq_id))
        f = result.scalar_one_or_none()
        if not f:
            return None

    return {
        "id": str(f.id),
        "question": f.question,
        "answer": f.answer,
        "variant_questions": f.variant_questions,
        "category": f.category,
        "card_types": f.card_types,
        "customer_tiers": f.customer_tiers,
        "keywords": f.keywords,
        "sort_order": f.sort_order,
        "doc_group": f.doc_group,
        "version": f.version,
        "approval_status": f.approval_status,
        "is_current_version": f.is_current_version,
        "effective_date": f.effective_date.isoformat() if f.effective_date else None,
        "expiry_date": f.expiry_date.isoformat() if f.expiry_date else None,
        "allowed_roles": f.allowed_roles,
        "regulatory_tags": f.regulatory_tags,
        "created_by": f.created_by,
        "updated_by": f.updated_by,
        "created_at": f.created_at.isoformat() if f.created_at else None,
        "updated_at": f.updated_at.isoformat() if f.updated_at else None,
    }


async def update_faq(
    session_factory: async_sessionmaker[AsyncSession],
    faq_id: str,
    *,
    question: str | None = None,
    answer: str | None = None,
    variant_questions: list[str] | None = None,
    category: str | None = None,
    card_types: list[str] | None = None,
    keywords: list[str] | None = None,
    sort_order: int | None = None,
    updated_by: str = "system",
) -> bool:
    """更新 FAQ"""
    async with session_factory() as session:
        result = await session.execute(select(KbFaq).where(KbFaq.id == faq_id))
        faq = result.scalar_one_or_none()
        if not faq:
            return False

        if question is not None:
            faq.question = question
        if answer is not None:
            faq.answer = answer
        if variant_questions is not None:
            faq.variant_questions = variant_questions
        if category is not None:
            faq.category = category
        if card_types is not None:
            faq.card_types = card_types
        if keywords is not None:
            faq.keywords = keywords
        if sort_order is not None:
            faq.sort_order = sort_order
        faq.updated_by = updated_by

        await session.commit()
    return True


async def delete_faq(
    session_factory: async_sessionmaker[AsyncSession],
    faq_id: str,
) -> bool:
    """软删除 FAQ"""
    async with session_factory() as session:
        result = await session.execute(select(KbFaq).where(KbFaq.id == faq_id))
        faq = result.scalar_one_or_none()
        if not faq:
            return False
        faq.is_deleted = True
        faq.is_current_version = False
        faq.approval_status = "ARCHIVED"
        await session.commit()
    return True


# ── 审批工作流 ──


_FAQ_TRANSITIONS = {
    "DRAFT": {"IN_REVIEW"},
    "IN_REVIEW": {"APPROVED", "REJECTED"},
    "APPROVED": {"PUBLISHED"},
    "REJECTED": {"DRAFT"},
    "PUBLISHED": {"SUPERSEDED", "ARCHIVED"},
    "SUPERSEDED": {"ARCHIVED"},
}


async def transition_faq_approval(
    session_factory: async_sessionmaker[AsyncSession],
    faq_id: str,
    target_status: str,
    *,
    actor_id: str,
    actor_role: str,
    comment: str = "",
    redis_client: aioredis.Redis | None = None,
) -> dict:
    """执行 FAQ 审批状态转换"""
    async with session_factory() as session:
        result = await session.execute(select(KbFaq).where(KbFaq.id == faq_id))
        faq = result.scalar_one_or_none()
        if not faq:
            from smartcs.shared.exceptions import SmartCSError

            raise SmartCSError(code=2001, message=f"FAQ 不存在: {faq_id}")

        current = faq.approval_status
        allowed = _FAQ_TRANSITIONS.get(current, set())
        if target_status not in allowed:
            from smartcs.shared.exceptions import SmartCSError

            raise SmartCSError(code=3005, message=f"非法审批转换: {current} → {target_status}")

        old_status = current
        faq.approval_status = target_status
        faq.updated_by = actor_id

        # 发布时: 同 doc_group 旧版本标记为 SUPERSEDED
        if target_status == "PUBLISHED" and faq.doc_group:
            old_result = await session.execute(
                select(KbFaq).where(
                    KbFaq.doc_group == faq.doc_group,
                    KbFaq.id != faq.id,
                    KbFaq.approval_status == "PUBLISHED",
                    KbFaq.is_deleted == False,  # noqa: E712
                )
            )
            for old_faq in old_result.scalars().all():
                old_faq.approval_status = "SUPERSEDED"
                old_faq.is_current_version = False

            faq.is_current_version = True

            # 写入 ES + Milvus 索引
            await _index_faq_to_search(faq, redis_client)

            # 预热精确匹配缓存
            await _warm_exact_match_cache(faq, redis_client)

        # 下线时: 清除缓存 + 删除索引
        if target_status in ("SUPERSEDED", "ARCHIVED"):
            await _remove_faq_from_cache(faq, redis_client)

        await session.commit()

    logger.info("FAQ 审批: %s %s→%s by=%s", faq_id, old_status, target_status, actor_id)
    return {"status": "ok", "faq_id": faq_id, "approval_status": target_status}


# ── 语义去重检测 ──


async def check_faq_duplicate(
    question: str,
    embedding_provider: Any,
    milvus_collection: Any,
    threshold: float = 0.92,
) -> list[dict]:
    """检测新建 FAQ 是否与已有 FAQ 语义重复

    利用 Milvus 向量检索，如果相似度超过 threshold 则认为是重复。
    """
    if not embedding_provider or not milvus_collection:
        return []

    try:
        query_embedding = await embedding_provider.embed_query(question)
        results = milvus_collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"nprobe": 16}},
            limit=5,
            expr='chunk_type == "faq_qa" and approval_status == "PUBLISHED"',
            output_fields=["chunk_id", "content", "category"],
        )

        duplicates = []
        for hit in results[0]:
            if hit.score >= threshold:
                duplicates.append(
                    {
                        "faq_id": hit.entity.get("chunk_id", ""),
                        "question": hit.entity.get("content", "")[:200],
                        "similarity": hit.score,
                    }
                )
        return duplicates
    except Exception as e:
        logger.warning("FAQ 去重检测失败: %s", e)
        return []


# ── 检索 ──


async def search_faq(
    query: str,
    redis_client: aioredis.Redis | None,
    embedding_provider: Any = None,
    milvus_collection: Any = None,
    *,
    user_role: str | None = None,
    card_type: str | None = None,
    top_k: int = 5,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    session_id: str | None = None,
) -> dict:
    """FAQ 检索（串行短路）

    1. 精确匹配（Redis 缓存）→ 命中直接返回
    2. 语义匹配（Milvus）→ top_k FAQ
    3. 未命中 → 返回空，调用方降级到通用 RAG

    Returns:
        {"match_type": "exact"|"semantic"|"miss", "results": [...]}
    """
    # 1. 精确匹配
    if redis_client:
        cache_k = _cache_key(query)
        cached = await redis_client.get(cache_k)
        if cached:
            import json

            faq_data = json.loads(cached)
            # 权限过滤
            if user_role and faq_data.get("allowed_roles") and user_role not in faq_data["allowed_roles"]:
                pass  # 无权限，降级到语义
            else:
                # 卡种过滤
                if card_type and faq_data.get("card_types") and card_type not in faq_data["card_types"]:
                    pass  # 不适用，降级到语义
                else:
                    await _log_search(session_factory, query, "exact", faq_data.get("id"), 1.0, user_role, session_id)
                    return {"match_type": "exact", "results": [faq_data]}

    # 2. 语义匹配
    if embedding_provider and milvus_collection:
        try:
            query_embedding = await embedding_provider.embed_query(query)
            results = milvus_collection.search(
                data=[query_embedding],
                anns_field="embedding",
                param={"metric_type": "COSINE", "params": {"nprobe": 16}},
                limit=top_k * 2,
                expr='chunk_type == "faq_qa" and approval_status == "PUBLISHED" and is_current_version == true',
                output_fields=["chunk_id", "content", "category", "card_types", "keywords"],
            )

            faq_results = []
            for hit in results[0]:
                entity = hit.entity
                # 卡种过滤
                card_types = entity.get("card_types", "")
                if card_type and card_types and card_type not in card_types:
                    continue

                faq_results.append(
                    {
                        "faq_id": entity.get("chunk_id", ""),
                        "question": entity.get("content", "")[:200],
                        "category": entity.get("category", ""),
                        "score": hit.score,
                    }
                )

                if len(faq_results) >= top_k:
                    break

            if faq_results:
                await _log_search(
                    session_factory,
                    query,
                    "semantic",
                    faq_results[0]["faq_id"],
                    faq_results[0]["score"],
                    user_role,
                    session_id,
                )
                return {"match_type": "semantic", "results": faq_results}
        except Exception as e:
            logger.warning("FAQ 语义检索失败: %s", e)

    # 3. 未命中
    await _log_search(session_factory, query, "miss", None, None, user_role, session_id)
    return {"match_type": "miss", "results": []}


# ── 索引 ──


async def _index_faq_to_search(faq: KbFaq, redis_client: aioredis.Redis | None) -> None:
    """将已发布 FAQ 索引到 ES + Milvus（发布时调用）

    FAQ 不走分块管道，直接将 question + variants 作为独立条目写入索引。
    """

    # 构建索引文档
    all_questions = [faq.question] + faq.variant_questions
    for q in all_questions:
        doc = {
            "chunk_id": str(faq.id),
            "doc_id": faq.doc_group or str(faq.id),
            "content": q,
            "answer": faq.answer,
            "category": faq.category,
            "card_types": ",".join(faq.card_types) if faq.card_types else "",
            "keywords": ",".join(faq.keywords) if faq.keywords else "",
            "chunk_type": "faq_qa",
            "approval_status": "PUBLISHED",
            "is_current_version": True,
            "effective_date": faq.effective_date.isoformat() if faq.effective_date else "",
            "expiry_date": faq.expiry_date.isoformat() if faq.expiry_date else "",
        }

    # 注意: 实际 ES + Milvus 写入需要 embedding_provider 和 es_client
    # 这里仅记录日志，实际索引在发布 API 中通过依赖注入完成
    logger.info("FAQ 索引就绪: id=%s question=%s variants=%d", faq.id, faq.question[:50], len(faq.variant_questions))


async def _warm_exact_match_cache(faq: KbFaq, redis_client: aioredis.Redis | None) -> None:
    """预热精确匹配缓存: 对 question + 所有 variant_questions 建立缓存"""
    import json

    if not redis_client:
        return

    faq_data = {
        "id": str(faq.id),
        "question": faq.question,
        "answer": faq.answer,
        "category": faq.category,
        "card_types": faq.card_types,
        "allowed_roles": faq.allowed_roles,
    }
    faq_json = json.dumps(faq_data, ensure_ascii=False)

    # 主问题 + 所有变体都建立缓存
    all_queries = [faq.question] + list(faq.variant_questions)
    for q in all_queries:
        cache_k = _cache_key(q)
        await redis_client.setex(cache_k, _FAQ_CACHE_TTL, faq_json)


async def _remove_faq_from_cache(faq: KbFaq, redis_client: aioredis.Redis | None) -> None:
    """FAQ 下线时清除缓存"""
    if not redis_client:
        return

    all_queries = [faq.question] + list(faq.variant_questions)
    for q in all_queries:
        cache_k = _cache_key(q)
        await redis_client.delete(cache_k)


async def _log_search(
    session_factory: async_sessionmaker[AsyncSession] | None,
    query: str,
    match_type: str,
    faq_id: str | None,
    score: float | None,
    user_role: str | None,
    session_id: str | None,
) -> None:
    """记录检索日志（用于分析）"""
    if not session_factory:
        return
    try:
        import uuid_utils

        async with session_factory() as session:
            log = KbFaqSearchLog(
                query=query[:512],
                match_type=match_type,
                faq_id=uuid_utils.UUID(faq_id) if faq_id else None,
                score=score,
                user_role=user_role,
                session_id=session_id,
            )
            session.add(log)
            await session.commit()
    except Exception:
        logger.debug("FAQ 检索日志写入失败")


# ── 自动过期 ──


async def expire_overdue_faqs(
    session_factory: async_sessionmaker[AsyncSession],
    redis_client: aioredis.Redis | None = None,
) -> int:
    """将过期的 PUBLISHED FAQ 自动下线

    定时任务调用（如每天凌晨）。
    Returns: 过期下线的 FAQ 数量
    """
    today = date.today()
    count = 0

    async with session_factory() as session:
        result = await session.execute(
            select(KbFaq).where(
                KbFaq.approval_status == "PUBLISHED",
                KbFaq.expiry_date < today,
                KbFaq.is_deleted == False,  # noqa: E712
            )
        )
        for faq in result.scalars().all():
            faq.approval_status = "SUPERSEDED"
            faq.is_current_version = False
            await _remove_faq_from_cache(faq, redis_client)
            count += 1

        if count:
            await session.commit()
            logger.info("自动过期 %d 条 FAQ", count)

    return count
