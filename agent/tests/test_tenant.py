"""多租户隔离模块单元测试 (tenant.py)"""

from __future__ import annotations

import pytest

from lumio.shared.tenant import (
    DEFAULT_TENANT_ID,
    TenantContext,
    TenantGuard,
    TenantIsolationError,
    get_default_tenant,
)

# ── TenantContext ──


def test_tenant_context_valid():
    """合法 tenant_id 可创建"""
    ctx = TenantContext(tenant_id="cmb", user_id="u1", role="agent")
    assert ctx.tenant_id == "cmb"
    assert ctx.role == "agent"


def test_tenant_context_defaults():
    """缺省字段有默认值"""
    ctx = TenantContext(tenant_id="cmb")
    assert ctx.user_id is None
    assert ctx.role == "user"
    assert ctx.permissions is None


def test_tenant_context_empty_id_rejected():
    """tenant_id 为空抛 ValueError"""
    with pytest.raises(ValueError, match="tenant_id 不能为空"):
        TenantContext(tenant_id="")


def test_tenant_context_invalid_format_rejected():
    """tenant_id 含大写/非法字符抛 ValueError"""
    with pytest.raises(ValueError, match="格式不合法"):
        TenantContext(tenant_id="CMB_@1")


# ── TenantGuard ──


def test_assert_same_tenant_pass():
    """同租户校验通过"""
    TenantGuard.assert_same_tenant("cmb", "cmb", operation="read_session")


def test_assert_same_tenant_mismatch_raises():
    """跨租户访问抛 TenantIsolationError (403)"""
    with pytest.raises(TenantIsolationError) as exc_info:
        TenantGuard.assert_same_tenant("cmb", "cib", operation="read_session")
    err = exc_info.value
    assert err.code == "TENANT_ISOLATION_VIOLATION"
    assert err.status_code == 403
    assert err.source_tenant == "cmb"
    assert err.target_tenant == "cib"


def test_make_redis_key():
    """Redis key 带 tenant 前缀"""
    assert TenantGuard.make_redis_key("cmb", "session:1") == "lumio:cmb:session:1"


def test_make_milvus_filter():
    """Milvus 过滤表达式"""
    assert TenantGuard.make_milvus_filter("cmb") == 'tenant_id == "cmb"'


def test_make_es_filter():
    """ES term query"""
    assert TenantGuard.make_es_filter("cmb") == {"term": {"tenant_id": "cmb"}}


# ── require_tenant / 默认租户 ──


def test_require_tenant_not_implemented():
    """require_tenant 是占位依赖, 未实现时抛 NotImplementedError"""
    from lumio.shared.tenant import require_tenant

    with pytest.raises(NotImplementedError):
        require_tenant()


def test_default_tenant():
    """开发模式默认租户"""
    assert DEFAULT_TENANT_ID == "default"
    assert get_default_tenant() == "default"
