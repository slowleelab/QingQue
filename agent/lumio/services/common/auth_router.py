"""认证与管理 API 路由

提供:
- POST /auth/login - 登录获取 JWT token（多用户，查 user_account 表）
- GET /auth/me - 获取当前用户信息
- GET /auth/users - 用户列表（admin）
- POST /auth/users - 创建用户（admin）
- PUT /auth/users/{id} - 更新用户（admin）
- DELETE /auth/users/{id} - 禁用用户（admin）
- POST /admin/rules/reload - 触发规则热加载
- GET/PUT /admin/sensitive-words - 敏感词管理
- GET /admin/stats - 业务统计
- GET /admin/dead-letter - 死信队列查看
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from lumio.shared.auth import (
    AuthUser,
    CurrentUser,
    Role,
    create_access_token,
)
from lumio.shared.exceptions import LumioError
from lumio.shared.orm_models import UserAccount, UserStatus
from lumio.shared.password import hash_password, verify_password
from lumio.shared.safety import safety_filter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


# ── 登录 ──


class LoginRequest(BaseModel):
    """登录请求"""

    username: str
    password: str


class LoginResponse(BaseModel):
    """登录响应"""

    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str
    role: str  # 来自 DB 的已校验角色字符串
    display_name: str | None = None


async def _ensure_default_admin(request: Request) -> None:
    """首次启动时初始化默认 admin 用户

    若 user_account 表无 admin 用户，且设置了 LUMIO_ADMIN_PASSWORD，
    则创建 username=admin 的默认管理员。
    """
    import os

    session_factory = getattr(request.app.state, "db_session_factory", None)
    if not session_factory:
        return

    async with session_factory() as session:
        result = await session.execute(select(UserAccount).where(UserAccount.role == "admin").limit(1))
        if result.scalar_one_or_none():
            return  # 已有 admin

        admin_password = os.getenv("LUMIO_ADMIN_PASSWORD", "")
        if not admin_password:
            logger.warning(
                "首次启动: 无 admin 用户且未设置 LUMIO_ADMIN_PASSWORD，" "请在配置后通过 init 脚本创建管理员"
            )
            return

        admin = UserAccount(
            username="admin",
            password_hash=hash_password(admin_password),
            role="admin",
            display_name="默认管理员",
        )
        session.add(admin)
        await session.commit()
        logger.info("首次启动: 已创建默认 admin 用户（username=admin）")


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request) -> LoginResponse:
    """用户登录，返回 JWT token

    多用户模式：查 user_account 表验证用户名+密码哈希。
    首次调用会自动初始化默认 admin（若 LUMIO_ADMIN_PASSWORD 已设置）。
    """
    await _ensure_default_admin(request)

    session_factory = getattr(request.app.state, "db_session_factory", None)
    if not session_factory:
        raise LumioError(code=5001, message="数据库未就绪，无法验证用户")

    async with session_factory() as session:
        result = await session.execute(select(UserAccount).where(UserAccount.username == body.username))
        user = result.scalar_one_or_none()

        if not user or not verify_password(body.password, user.password_hash):
            raise LumioError(code=3001, message="用户名或密码错误")

        if user.status == UserStatus.DISABLED.value:
            raise LumioError(code=3002, message="账号已禁用")

        token = create_access_token(str(user.id), user.role)
        return LoginResponse(
            access_token=token,
            user_id=str(user.id),
            username=user.username,
            role=user.role,
            display_name=user.display_name,
        )


@router.get("/auth/me")
async def get_me(user: CurrentUser):
    """获取当前登录用户信息"""
    return {"user_id": user.user_id, "role": user.role, "session_id": user.session_id}


# ── 用户管理（admin）──


class UserCreateRequest(BaseModel):
    """创建用户请求"""

    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    role: Role = "customer"
    display_name: str | None = None


class UserUpdateRequest(BaseModel):
    """更新用户请求"""

    password: str | None = Field(None, min_length=6, max_length=128)
    role: Role | None = None
    status: str | None = None
    display_name: str | None = None


class UserResponse(BaseModel):
    """用户信息响应（不含密码）"""

    id: str
    username: str
    role: str  # 来自 DB 的已校验角色字符串
    status: str
    display_name: str | None
    created_at: str | None = None


@router.get("/auth/users", response_model=list[UserResponse])
async def list_users(request: Request, user: CurrentUser) -> list[UserResponse]:
    """用户列表（仅 admin）"""
    if user.role != "admin":
        raise LumioError(code=1003, message="权限不足，需要 admin 角色")

    session_factory = getattr(request.app.state, "db_session_factory", None)
    if not session_factory:
        raise LumioError(code=5001, message="数据库未就绪")

    async with session_factory() as session:
        result = await session.execute(select(UserAccount).order_by(UserAccount.created_at))
        users = result.scalars().all()

    return [
        UserResponse(
            id=str(u.id),
            username=u.username,
            role=u.role,
            status=u.status,
            display_name=u.display_name,
            created_at=u.created_at.isoformat() if u.created_at else None,
        )
        for u in users
    ]


@router.post("/auth/users", response_model=UserResponse, status_code=201)
async def create_user(body: UserCreateRequest, request: Request, user: CurrentUser) -> UserResponse:
    """创建用户（仅 admin）"""
    if user.role != "admin":
        raise LumioError(code=1003, message="权限不足，需要 admin 角色")

    session_factory = getattr(request.app.state, "db_session_factory", None)
    if not session_factory:
        raise LumioError(code=5001, message="数据库未就绪")

    async with session_factory() as session:
        # 检查用户名唯一
        existing = await session.execute(select(UserAccount).where(UserAccount.username == body.username))
        if existing.scalar_one_or_none():
            raise LumioError(code=3003, message=f"用户名已存在: {body.username}")

        new_user = UserAccount(
            username=body.username,
            password_hash=hash_password(body.password),
            role=body.role,
            display_name=body.display_name,
        )
        session.add(new_user)
        await session.commit()
        await session.refresh(new_user)

    logger.info("用户创建: %s role=%s by=%s", body.username, body.role, user.user_id)
    return UserResponse(
        id=str(new_user.id),
        username=new_user.username,
        role=new_user.role,
        status=new_user.status,
        display_name=new_user.display_name,
        created_at=new_user.created_at.isoformat() if new_user.created_at else None,
    )


@router.put("/auth/users/{user_id}", response_model=UserResponse)
async def update_user(user_id: str, body: UserUpdateRequest, request: Request, current: CurrentUser) -> UserResponse:
    """更新用户（仅 admin）"""
    if current.role != "admin":
        raise LumioError(code=1003, message="权限不足，需要 admin 角色")

    session_factory = getattr(request.app.state, "db_session_factory", None)
    if not session_factory:
        raise LumioError(code=5001, message="数据库未就绪")

    async with session_factory() as session:
        result = await session.execute(select(UserAccount).where(UserAccount.id == user_id))
        target = result.scalar_one_or_none()
        if not target:
            raise LumioError(code=2001, message=f"用户不存在: {user_id}")

        if body.password:
            target.password_hash = hash_password(body.password)
        if body.role:
            target.role = body.role
        if body.status:
            if body.status not in (UserStatus.ACTIVE.value, UserStatus.DISABLED.value):
                raise LumioError(code=2001, message=f"无效状态: {body.status}")
            target.status = body.status
        if body.display_name is not None:
            target.display_name = body.display_name

        await session.commit()
        await session.refresh(target)

    logger.info("用户更新: %s by=%s", user_id, current.user_id)
    return UserResponse(
        id=str(target.id),
        username=target.username,
        role=target.role,
        status=target.status,
        display_name=target.display_name,
        created_at=target.created_at.isoformat() if target.created_at else None,
    )


@router.delete("/auth/users/{user_id}")
async def delete_user(user_id: str, request: Request, current: CurrentUser) -> dict[str, str]:
    """禁用用户（软删除，仅 admin）

    不物理删除以保留审计关联。管理员不能禁用自己。
    """
    if current.role != "admin":
        raise LumioError(code=1003, message="权限不足，需要 admin 角色")
    if user_id == current.user_id:
        raise LumioError(code=3004, message="不能禁用当前登录用户")

    session_factory = getattr(request.app.state, "db_session_factory", None)
    if not session_factory:
        raise LumioError(code=5001, message="数据库未就绪")

    async with session_factory() as session:
        result = await session.execute(select(UserAccount).where(UserAccount.id == user_id))
        target = result.scalar_one_or_none()
        if not target:
            raise LumioError(code=2001, message=f"用户不存在: {user_id}")

        target.status = UserStatus.DISABLED.value
        await session.commit()

    logger.info("用户禁用: %s by=%s", user_id, current.user_id)
    return {"result": "ok", "user_id": user_id, "status": "disabled"}


# ── 敏感词管理 ──


class SensitiveWordsResponse(BaseModel):
    words: list[str]
    count: int


class SensitiveWordsUpdate(BaseModel):
    words: list[str]


@router.get("/admin/sensitive-words", response_model=SensitiveWordsResponse)
async def get_sensitive_words(user: CurrentUser):
    """获取当前敏感词列表"""
    return SensitiveWordsResponse(
        words=sorted(safety_filter.words),
        count=len(safety_filter.words),
    )


@router.put("/admin/sensitive-words", response_model=SensitiveWordsResponse)
async def update_sensitive_words(body: SensitiveWordsUpdate, request: Request, user: CurrentUser):
    """更新敏感词列表并通知所有实例热加载"""
    safety_filter.load_from_set(set(body.words))

    # 发布热更新通知
    redis = getattr(request.app.state, "redis_client", None)
    if redis:
        await redis.delete("lumio:safety:words")
        if body.words:
            await redis.sadd("lumio:safety:words", *body.words)
        await redis.publish("lumio:safety:reload", "update")

    logger.info("敏感词已更新: %d 个 (by %s)", len(body.words), user.user_id)
    return SensitiveWordsResponse(
        words=sorted(safety_filter.words),
        count=len(safety_filter.words),
    )


# ── 规则热加载 ──


@router.post("/admin/rules/reload")
async def reload_rules(request: Request):
    """触发意图规则热加载"""
    redis = getattr(request.app.state, "redis_client", None)
    if redis:
        await redis.publish("lumio:rules:reload", json.dumps({"action": "reload"}))
    return {"status": "ok", "message": "规则热加载通知已发布"}


# ── 业务统计 ──


@router.get("/admin/stats")
async def get_stats(request: Request):
    """获取业务统计概览"""
    redis = getattr(request.app.state, "redis_client", None)
    stats: dict = {"sessions": {}, "messages": {}, "performance": {}}

    if redis:
        # 会话统计
        session_keys = await redis.keys("lumio:session:*:meta")
        stats["sessions"]["total_active"] = len(session_keys)

        # 消息队列统计
        stream_len = await redis.xlen("lumio:chat:stream")
        stats["messages"]["stream_length"] = stream_len

        # 死信队列统计
        dl_len = await redis.xlen("lumio:chat:dead_letter")
        stats["messages"]["dead_letter_count"] = dl_len

    # Bot 运行时指标
    try:
        from lumio.services.bot.router import _metrics

        stats["performance"]["fast_reply_total"] = _metrics.get("fr", 0)
        stats["performance"]["timeout_total"] = _metrics.get("to", 0)
        stats["performance"]["merge_total"] = _metrics.get("mg", 0)
    except Exception:
        pass

    return stats


# ── 死信队列 ──


@router.get("/admin/dead-letter")
async def get_dead_letters(request: Request, count: int = 20):
    """查看死信队列中的失败消息"""
    redis = getattr(request.app.state, "redis_client", None)
    if not redis:
        return {"messages": [], "total": 0}

    total = await redis.xlen("lumio:chat:dead_letter")
    entries = await redis.xrevrange("lumio:chat:dead_letter", count=count)

    messages = []
    for msg_id, fields in entries:
        msg = {
            "id": msg_id if isinstance(msg_id, str) else msg_id.decode(),
        }
        for k, v in fields.items():
            key = k.decode() if isinstance(k, bytes) else k
            val = v.decode() if isinstance(v, bytes) else v
            msg[key] = val
        messages.append(msg)

    return {"messages": messages, "total": total}


# ── 文档审批工作流 ──


class ApprovalActionRequest(BaseModel):
    """审批操作请求"""

    comment: str = ""


_VALID_TRANSITIONS = {
    "DRAFT": {"IN_REVIEW"},
    "IN_REVIEW": {"APPROVED", "REJECTED"},
    "APPROVED": {"PUBLISHED"},
    "REJECTED": {"DRAFT"},
    "PUBLISHED": {"SUPERSEDED", "ARCHIVED"},
    "SUPERSEDED": {"ARCHIVED"},
}


@router.post("/kb/documents/{doc_id}/submit")
async def submit_for_review(doc_id: str, body: ApprovalActionRequest, request: Request, user: CurrentUser):
    """提交文档审核 (DRAFT → IN_REVIEW)"""
    return await _transition_approval(doc_id, "IN_REVIEW", body.comment, request, user)


@router.post("/kb/documents/{doc_id}/approve")
async def approve_document(doc_id: str, body: ApprovalActionRequest, request: Request, user: CurrentUser):
    """审核通过 (IN_REVIEW → APPROVED)"""
    return await _transition_approval(doc_id, "APPROVED", body.comment, request, user)


@router.post("/kb/documents/{doc_id}/reject")
async def reject_document(doc_id: str, body: ApprovalActionRequest, request: Request, user: CurrentUser):
    """审核驳回 (IN_REVIEW → REJECTED)"""
    return await _transition_approval(doc_id, "REJECTED", body.comment, request, user)


@router.post("/kb/documents/{doc_id}/publish")
async def publish_document(doc_id: str, body: ApprovalActionRequest, request: Request, user: CurrentUser):
    """发布文档 (APPROVED → PUBLISHED)

    发布后文档可被检索系统引用。
    如果同一 doc_group 有其他 PUBLISHED 文档，自动将其标记为 SUPERSEDED。
    """
    return await _transition_approval(doc_id, "PUBLISHED", body.comment, request, user)


@router.post("/kb/documents/{doc_id}/archive")
async def archive_document(doc_id: str, body: ApprovalActionRequest, request: Request, user: CurrentUser):
    """归档文档 (→ ARCHIVED)"""
    return await _transition_approval(doc_id, "ARCHIVED", body.comment, request, user)


@router.get("/kb/documents/{doc_id}/approvals")
async def get_approval_history(doc_id: str, request: Request):
    """查看文档审批历史"""
    from sqlalchemy import select

    from lumio.shared.orm_models import KbDocumentApproval

    session_factory = getattr(request.app.state, "db_session_factory", None)
    if not session_factory:
        return {"approvals": []}

    async with session_factory() as session:
        result = await session.execute(
            select(KbDocumentApproval)
            .where(KbDocumentApproval.document_id == doc_id)
            .order_by(KbDocumentApproval.created_at)
        )
        records = result.scalars().all()

    return {
        "approvals": [
            {
                "action": r.action.value,
                "from_status": r.from_status,
                "to_status": r.to_status,
                "actor_id": r.actor_id,
                "actor_role": r.actor_role,
                "comment": r.comment,
                "timestamp": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ]
    }


async def _transition_approval(
    doc_id: str,
    target_status: str,
    comment: str,
    request: Request,
    user: AuthUser,
) -> JSONResponse:
    """执行审批状态转换（通用逻辑）"""
    from sqlalchemy import select

    from lumio.shared.orm_models import KbApprovalAction, KbDocument, KbDocumentApproval

    session_factory = getattr(request.app.state, "db_session_factory", None)
    if not session_factory:
        raise LumioError(code=5001, message="数据库未就绪")

    async with session_factory() as session:
        result = await session.execute(select(KbDocument).where(KbDocument.id == doc_id))
        doc = result.scalar_one_or_none()
        if not doc:
            raise LumioError(code=2001, message=f"文档不存在: {doc_id}")

        current_status = (
            doc.approval_status.value if hasattr(doc.approval_status, "value") else str(doc.approval_status)
        )

        # 校验状态转换合法性
        allowed = _VALID_TRANSITIONS.get(current_status, set())
        if target_status not in allowed:
            raise LumioError(
                code=3005,
                message=f"非法审批状态转换: {current_status} → {target_status}",
            )

        # 执行转换
        old_status = current_status
        doc.approval_status = target_status
        doc.updated_by = user.user_id

        # 发布时: 将同 doc_group 的旧 PUBLISHED 文档标记为 SUPERSEDED
        if target_status == "PUBLISHED" and doc.doc_group:
            old_result = await session.execute(
                select(KbDocument).where(
                    KbDocument.doc_group == doc.doc_group,
                    KbDocument.id != doc.id,
                    KbDocument.approval_status == "PUBLISHED",
                    KbDocument.is_deleted == False,  # noqa: E712
                )
            )
            for old_doc in old_result.scalars().all():
                old_doc.approval_status = "SUPERSEDED"
                old_doc.is_current_version = False
                # 记录审批日志
                session.add(
                    KbDocumentApproval(
                        document_id=old_doc.id,
                        action=KbApprovalAction.SUPERSEDE,
                        from_status="PUBLISHED",
                        to_status="SUPERSEDED",
                        actor_id=user.user_id,
                        actor_role=user.role,
                        comment=f"被新版本 {doc.version} 替代",
                    )
                )

        # 标记当前版本
        if target_status == "PUBLISHED":
            doc.is_current_version = True

        # 记录审批日志
        action_map = {
            "IN_REVIEW": KbApprovalAction.SUBMIT,
            "APPROVED": KbApprovalAction.APPROVE,
            "REJECTED": KbApprovalAction.REJECT,
            "PUBLISHED": KbApprovalAction.PUBLISH,
            "ARCHIVED": KbApprovalAction.ARCHIVE,
            "SUPERSEDED": KbApprovalAction.SUPERSEDE,
        }
        session.add(
            KbDocumentApproval(
                document_id=doc.id,
                action=action_map.get(target_status, KbApprovalAction.APPROVE),
                from_status=old_status,
                to_status=target_status,
                actor_id=user.user_id,
                actor_role=user.role,
                comment=comment,
            )
        )

        await session.commit()

    logger.info(
        "文档审批: doc=%s %s→%s by=%s role=%s comment=%s",
        doc_id,
        old_status,
        target_status,
        user.user_id,
        user.role,
        comment,
    )
    return JSONResponse(
        content={
            "status": "ok",
            "doc_id": doc_id,
            "approval_status": target_status,
        }
    )
