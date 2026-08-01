"""JWT 鉴权测试 (I1-C2)

覆盖:
- JWT 签发 / 校验 / 过期 / audience / issuer 错误
- API Key 兜底
- JWT 失败时 fall back
- Principal 字段映射
- _looks_like_jwt 区分
- _infer_role 推断
- require_role 守卫
"""

from __future__ import annotations

import time
from typing import Any

import pytest
import jwt as pyjwt

pytest.importorskip("fastapi", reason="需要 fastapi 才能测鉴权")
pytest.importorskip("elasticsearch", reason="engine.py import elasticsearch")

from fastapi import HTTPException, Request  # noqa: E402

from kb.security.auth import (  # noqa: E402
    Principal,
    _infer_role,
    _looks_like_api_key,
    _looks_like_jwt,
    _verify_jwt,
    verify_principal,
    require_role,
)


# ── 测试夹具 ──


@pytest.fixture
def jwt_settings(monkeypatch: pytest.MonkeyPatch) -> Any:
    """配置 JWT (使用 dev 密钥)"""
    from kb.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("JWT_SECRET", "test-secret-12345-padding-padding-padding")
    monkeypatch.setenv("JWT_AUDIENCE", "kb-service")
    monkeypatch.setenv("JWT_ISSUER", "smartcs-auth")
    monkeypatch.setenv("KB_ENVIRONMENT", "development")
    return get_settings()


def _make_token(
    secret: str,
    *,
    sub: str = "user-1",
    tenant_id: str = "bank-a",
    roles: list[str] | None = None,
    actor_role: str | None = None,
    tier: str = "normal",
    aud: str = "kb-service",
    iss: str = "smartcs-auth",
    exp_offset: int = 3600,
    include_exp_iat: bool = True,
    algorithm: str = "HS256",
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": sub,
        "tenant_id": tenant_id,
        "roles": roles or ["editor"],
        "tier": tier,
    }
    if actor_role is not None:
        payload["actor_role"] = actor_role
    if include_exp_iat:
        payload["exp"] = now + exp_offset
        payload["iat"] = now
    # PyJWT 2.13: aud/iss 必须放 payload (encode 不再支持 keyword)
    if aud:
        payload["aud"] = aud
    if iss:
        payload["iss"] = iss
    return pyjwt.encode(payload, secret, algorithm=algorithm)


# ── JWT 解析 ──


def test_verify_jwt_happy_path(jwt_settings: Any) -> None:
    token = _make_token("test-secret-12345-padding-padding-padding", sub="alice", roles=["editor"])
    p = _verify_jwt(token)
    assert p.actor_id == "alice"
    assert p.tenant_id == "bank-a"
    assert p.roles == ["editor"]
    assert p.actor_role == "editor"  # 从 roles 推断
    assert p.tier == "normal"
    assert p.auth_method == "jwt"


def test_verify_jwt_uses_explicit_actor_role(jwt_settings: Any) -> None:
    token = _make_token(
        "test-secret-12345-padding-padding-padding", sub="bob", roles=["admin", "editor"], actor_role="admin"
    )
    p = _verify_jwt(token)
    assert p.actor_role == "admin"  # 显式 > 推断


def test_verify_jwt_default_tenant(jwt_settings: Any) -> None:
    """tenant_id claim 缺省时用 settings.security.default_tenant_id"""
    now = int(time.time())
    payload = {
        "sub": "x", "exp": now + 3600, "iat": now, "roles": [],
        "aud": "kb-service", "iss": "smartcs-auth",
    }
    token = pyjwt.encode(payload, "test-secret-12345-padding-padding-padding", algorithm="HS256")
    p = _verify_jwt(token)
    assert p.tenant_id == "default"  # 从 default_tenant_id 来


def test_verify_jwt_expired(jwt_settings: Any) -> None:
    # 用 -100s 超过 leeway 30s, 必过期
    token = _make_token("test-secret-12345-padding-padding-padding", exp_offset=-100)
    with pytest.raises(HTTPException) as exc:
        _verify_jwt(token)
    assert exc.value.status_code == 401
    assert "过期" in exc.value.detail


def test_verify_jwt_wrong_audience(jwt_settings: Any) -> None:
    token = _make_token("test-secret-12345-padding-padding-padding", aud="other-service")
    with pytest.raises(HTTPException) as exc:
        _verify_jwt(token)
    assert exc.value.status_code == 401


def test_verify_jwt_wrong_issuer(jwt_settings: Any) -> None:
    token = _make_token("test-secret-12345-padding-padding-padding", iss="evil-issuer")
    with pytest.raises(HTTPException) as exc:
        _verify_jwt(token)
    assert exc.value.status_code == 401


def test_verify_jwt_missing_sub(jwt_settings: Any) -> None:
    now = int(time.time())
    payload = {
        "exp": now + 3600, "iat": now, "roles": [],
        "aud": "kb-service", "iss": "smartcs-auth",
    }  # 无 sub
    token = pyjwt.encode(payload, "test-secret-12345-padding-padding-padding", algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        _verify_jwt(token)
    assert exc.value.status_code == 401
    assert "sub" in exc.value.detail


def test_verify_jwt_wrong_signature(jwt_settings: Any) -> None:
    token = _make_token("wrong-secret")
    with pytest.raises(HTTPException) as exc:
        _verify_jwt(token)
    assert exc.value.status_code == 401


def test_verify_jwt_roles_must_be_list(jwt_settings: Any) -> None:
    now = int(time.time())
    payload = {
        "sub": "x", "exp": now + 3600, "iat": now,
        "roles": "admin",  # 错: 应是 list
        "aud": "kb-service", "iss": "smartcs-auth",
    }
    token = pyjwt.encode(payload, "test-secret-12345-padding-padding-padding", algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        _verify_jwt(token)
    assert exc.value.status_code == 401
    assert "roles" in exc.value.detail


# ── 工具函数 ──


def test_looks_like_jwt() -> None:
    assert _looks_like_jwt("a.b.c")
    assert _looks_like_jwt("eyJhbGci.eyJzdWIi.signature")
    assert not _looks_like_jwt("plain-api-key")
    assert not _looks_like_jwt("only.one")  # 1 个点不算
    assert not _looks_like_jwt("no-dots")


def test_looks_like_api_key() -> None:
    assert _looks_like_api_key("plain-api-key")
    assert _looks_like_api_key("only.one")  # 0 或 1 个点算 API Key
    assert not _looks_like_api_key("a.b.c")  # 2 个点是 JWT


def test_infer_role() -> None:
    # 优先级: admin > reviewer > editor > auditor > user
    assert _infer_role(["admin"]) == "admin"
    assert _infer_role(["admin", "editor"]) == "admin"
    assert _infer_role(["reviewer", "editor"]) == "reviewer"
    assert _infer_role(["editor"]) == "editor"
    assert _infer_role(["auditor"]) == "auditor"
    assert _infer_role(["other"]) == "user"
    assert _infer_role([]) == "user"


# ── require_role 守卫 ──


@pytest.mark.asyncio
async def test_require_role_allows_matching_role() -> None:
    guard = require_role("admin", "reviewer")
    p = Principal(
        actor_id="u1", actor_role="admin", tenant_id="t1",
        roles=["admin"], auth_method="jwt",
    )
    result = await guard(p)
    assert result.actor_id == "u1"


@pytest.mark.asyncio
async def test_require_role_rejects_non_matching() -> None:
    guard = require_role("admin")
    p = Principal(
        actor_id="u1", actor_role="editor", tenant_id="t1",
        roles=["editor"], auth_method="jwt",
    )
    with pytest.raises(HTTPException) as exc:
        await guard(p)
    assert exc.value.status_code == 403
    assert "admin" in exc.value.detail


# ── verify_principal 集成 (mock Request) ──


@pytest.mark.asyncio
async def test_verify_principal_jwt_wins(jwt_settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """JWT 模式下, JWT 优先 API Key"""
    from fastapi.security import HTTPAuthorizationCredentials

    token = _make_token("test-secret-12345-padding-padding-padding", sub="alice")
    request = Request({"type": "http", "headers": []})
    creds = HTTPAuthorizationCredentials(scheme="bearer", credentials=token)
    p = await verify_principal(request, creds)
    assert p.actor_id == "alice"
    assert p.auth_method == "jwt"
    assert request.state.principal is p


@pytest.mark.asyncio
async def test_verify_principal_no_header(jwt_settings: Any) -> None:
    request = Request({"type": "http", "headers": []})
    with pytest.raises(HTTPException) as exc:
        await verify_principal(request, None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_principal_non_bearer(jwt_settings: Any) -> None:
    """Basic auth 不接受"""
    from fastapi.security import HTTPAuthorizationCredentials

    request = Request({"type": "http", "headers": []})
    creds = HTTPAuthorizationCredentials(scheme="basic", credentials="user:pass")
    with pytest.raises(HTTPException) as exc:
        await verify_principal(request, creds)
    assert exc.value.status_code == 401


# ── 多租户隔离场景 ──


def test_retrieve_request_has_tenant_and_role_fields() -> None:
    """RetrieveRequest 必须支持 tenant_id / actor_roles 字段 (I1-C2)"""
    from kb.retrieval.models import RetrieveRequest

    req = RetrieveRequest(
        query="信用卡年费",
        tenant_id="bank-a",
        actor_roles=["cs", "manager"],
    )
    assert req.tenant_id == "bank-a"
    assert req.actor_roles == ["cs", "manager"]
    # 缺省场景
    req2 = RetrieveRequest(query="x")
    assert req2.tenant_id is None
    assert req2.actor_roles == []
    assert req2.exclude == {}
    assert req2.timeout_ms == 1500


def test_retrieve_response_has_degraded_fields() -> None:
    """RetrieveResponse 必须支持 degraded 标记"""
    from kb.retrieval.models import RetrieveResponse

    resp = RetrieveResponse(
        results=[],
        total_candidates=0,
        latency_ms=50,
        degraded=True,
        degraded_stages=["hybrid→bm25_only"],
    )
    assert resp.degraded is True
    assert resp.degraded_stages == ["hybrid→bm25_only"]


# ── Engine exclude 工具 ──


def test_build_es_excludes_term() -> None:
    from kb.retrieval.engine import build_es_excludes

    clauses = build_es_excludes({"doc_type": "marketing"})
    assert clauses == [{"term": {"doc_type": "marketing"}}]


def test_build_es_excludes_terms_list() -> None:
    from kb.retrieval.engine import build_es_excludes

    clauses = build_es_excludes({"doc_type": ["marketing", "spam"]})
    assert clauses == [{"terms": {"doc_type": ["marketing", "spam"]}}]


def test_build_es_excludes_skips_empty() -> None:
    from kb.retrieval.engine import build_es_excludes

    assert build_es_excludes({}) == []
    assert build_es_excludes({"doc_type": []}) == []
    assert build_es_excludes({"doc_type": None}) == []


def test_build_es_filters_tenant_id_in_keyword_set() -> None:
    """tenant_id 必须在 _ES_KEYWORD_FIELDS 里, 走 term 过滤"""
    from kb.retrieval.engine import _ES_KEYWORD_FIELDS, build_es_filters

    assert "tenant_id" in _ES_KEYWORD_FIELDS
    clauses = build_es_filters({"tenant_id": "bank-a", "approval_status": "PUBLISHED"})
    assert {"term": {"tenant_id": "bank-a"}} in clauses
    assert {"term": {"approval_status": "PUBLISHED"}} in clauses
