"""E3: 多租户隔离.

场景: 招行客户信息不能给建行客服看到.

实现:
1. 每个数据查询/写入都带 tenant_id 过滤
2. 跨 tenant 查询硬错误 (tenant_mismatch 异常)
3. PostgreSQL Row-Level Security (RLS) 兜底
4. Redis key 加 tenant 前缀
5. Milvus / ES 加 tenant 字段

middleware 校验:
- request.tenant_id (从 JWT / API key 提取)
- 与请求参数 / body 中的 tenant_id 对比
- 不匹配 → 403
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from lumio.shared.exceptions import LumioError as LumioException  # alias
from lumio.shared.logger import get_logger

logger = get_logger(__name__)


class TenantIsolationError(LumioException):
    """跨租户访问异常."""

    def __init__(self, message: str, source_tenant: str, target_tenant: str) -> None:
        super().__init__(
            code="TENANT_ISOLATION_VIOLATION",
            message=message,
            status_code=403,
        )
        self.source_tenant = source_tenant
        self.target_tenant = target_tenant


@dataclass
class TenantContext:
    """租户上下文 (请求级)."""

    tenant_id: str
    user_id: str | None = None
    role: str = "user"  # user / agent / admin
    permissions: list[str] | None = None

    def __post_init__(self) -> None:
        if not self.tenant_id:
            raise ValueError("tenant_id 不能为空")
        if not re.match(r"^[a-z0-9_-]{1,64}$", self.tenant_id):
            raise ValueError(f"tenant_id 格式不合法: {self.tenant_id}")


class TenantGuard:
    """租户隔离守卫."""

    @staticmethod
    def assert_same_tenant(
        source_tenant: str,
        target_tenant: str,
        operation: str = "access",
    ) -> None:
        """校验两个 tenant_id 必须一致, 否则抛 TenantIsolationError.

        用法:
            TenantGuard.assert_same_tenant(
                source_tenant=request.state.tenant_id,
                target_tenant=session.tenant_id,
                operation="read_session",
            )
        """
        if source_tenant != target_tenant:
            logger.error(
                "租户隔离违规: source=%s target=%s op=%s",
                source_tenant,
                target_tenant,
                operation,
            )
            raise TenantIsolationError(
                f"租户隔离违规: {operation} (source={source_tenant}, target={target_tenant})",
                source_tenant=source_tenant,
                target_tenant=target_tenant,
            )

    @staticmethod
    def make_redis_key(tenant_id: str, key_suffix: str) -> str:
        """生成 tenant-scoped Redis key."""
        return f"lumio:{tenant_id}:{key_suffix}"

    @staticmethod
    def make_milvus_filter(tenant_id: str) -> str:
        """生成 Milvus tenant 过滤表达式."""
        return f'tenant_id == "{tenant_id}"'

    @staticmethod
    def make_es_filter(tenant_id: str) -> dict[str, Any]:
        """生成 ES tenant 过滤 term query."""
        return {"term": {"tenant_id": tenant_id}}


# ── 装饰器: 自动注入 tenant 校验 ──


def require_tenant(extract_tenant: str = "tenant_id") -> Any:
    """FastAPI 依赖: 提取并校验 tenant_id.

    用法:
        @app.get("/api/sessions/{session_id}")
        async def get_session(
            session_id: str,
            tenant: TenantContext = Depends(require_tenant()),
        ):
            ...
    """
    # 实际实现需要 FastAPI Depends + Request
    # 此处提供类型提示
    raise NotImplementedError("需要 FastAPI Request 上下文")


# ── 已知默认 tenant (开发/单租户模式) ──
DEFAULT_TENANT_ID = "default"


def get_default_tenant() -> str:
    """获取默认 tenant_id (开发模式)."""
    return DEFAULT_TENANT_ID
