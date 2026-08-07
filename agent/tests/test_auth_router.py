"""认证/管理端点 ASGI 单元测试 (auth_router.py, 全 mock 无中间件)"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lumio.services.common.auth_router import router as auth_router
from lumio.shared.auth import AuthUser, get_current_user
from lumio.shared.middleware import register_exception_handlers
from lumio.shared.password import hash_password


@pytest.fixture
def app() -> FastAPI:
    """最小 FastAPI app: 挂 auth_router + mock app.state"""
    app = FastAPI()
    app.include_router(auth_router, prefix="/api")
    register_exception_handlers(app)  # LumioError → 统一 JSON 错误响应

    # 认证覆盖: 固定 admin 用户
    app.dependency_overrides[get_current_user] = lambda: AuthUser(user_id="admin-1", role="admin", session_id="s1")
    return app


class _FakeUser:
    def __init__(self, username="admin", role="admin", status="active"):
        self.id = "11111111-2222-3333-4444-555555555555"
        self.username = username
        self.role = role
        self.status = status
        self.display_name = "管理员"
        self.password_hash = "x"
        self.created_at = None


class _FakeDbSession:
    """mock DB session: execute 返回可配置结果"""

    def __init__(self, one=None, all_rows=None, scalar=None):
        self.one = one
        self.all_rows = all_rows or []
        self.scalar_val = scalar
        self.added = []
        self.committed = False
        self.refreshed = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, stmt, *a, **kw):
        r = MagicMock()
        if self.scalar_val is not None:
            r.scalar.return_value = self.scalar_val
        if "count" in str(stmt):
            r.scalar.return_value = self.scalar_val if self.scalar_val is not None else 1
        r.scalar_one_or_none.return_value = self.one
        r.scalars.return_value.all.return_value = self.all_rows
        return r

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        # 模拟 DB server_default 回填
        if getattr(obj, "status", None) is None:
            obj.status = "active"
        if getattr(obj, "id", None) is None:
            obj.id = "11111111-2222-3333-4444-555555555555"
        self.refreshed.append(obj)


class _EmptyIter:
    """空异步迭代器"""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


@pytest.fixture
def setup_state(app: FastAPI, monkeypatch) -> None:
    """给 app.state 注入 mock 依赖"""
    from lumio.services.common import auth_router as ar_mod

    # 注入 fake db_session_factory (可被测试替换)
    db = _FakeDbSession(one=_FakeUser())
    app.state.db_session_factory = lambda: db

    redis = AsyncMock()
    redis.scan_iter = MagicMock(return_value=_EmptyIter())  # 同步返回迭代器 (async for 直接消费)
    redis.xlen = AsyncMock(return_value=0)
    redis.xrevrange = AsyncMock(return_value=[])
    redis.publish = AsyncMock()
    redis.delete = AsyncMock()
    redis.sadd = AsyncMock()
    app.state.redis_client = redis

    # 敏感词加载独立于全局单例
    from lumio.shared.safety import SafetyFilter

    sf = SafetyFilter()
    sf.load_from_set({"保本保息"})
    ar_mod.safety_filter = sf

    yield db, redis


async def _client(app: FastAPI) -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── 登录 ──


async def test_login_success(app: FastAPI, setup_state) -> None:
    """登录成功返回 token"""
    db, _ = setup_state
    db.one = _FakeUser(username="admin")
    db.one.password_hash = hash_password("admin123")
    async with await _client(app) as c:
        resp = await c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data


async def test_login_wrong_password(app: FastAPI, setup_state) -> None:
    """密码错误 → 401 业务错误"""
    db, _ = setup_state
    db.one = None  # 用户不存在
    async with await _client(app) as c:
        resp = await c.post("/api/auth/login", json={"username": "nobody", "password": "x"})
    # 业务错误码 3001 → HTTP 422 (3xxx 映射规则)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == 3001


async def test_login_no_db(app: FastAPI, setup_state) -> None:
    """DB 未就绪 → 5001"""
    app.state.db_session_factory = None
    async with await _client(app) as c:
        resp = await c.post("/api/auth/login", json={"username": "a", "password": "b"})
    assert resp.status_code in (500, 503)
    assert "error" in resp.json()


# ── 用户管理 ──


async def test_list_users(app: FastAPI, setup_state) -> None:
    """用户列表"""
    db, _ = setup_state
    db.all_rows = [_FakeUser(username="u1"), _FakeUser(username="u2", role="agent")]
    async with await _client(app) as c:
        resp = await c.get("/api/auth/users")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_create_user_duplicate(app: FastAPI, setup_state) -> None:
    """用户名重复 → 3003"""
    db, _ = setup_state
    db.one = _FakeUser(username="dup")
    async with await _client(app) as c:
        resp = await c.post(
            "/api/auth/users",
            json={"username": "dup", "password": "pw123456", "role": "agent"},
        )
    # 3003 → 422
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == 3003


async def test_create_user_success(app: FastAPI, setup_state) -> None:
    """创建用户成功"""
    db, _ = setup_state
    db.one = None
    db.all_rows = []
    async with await _client(app) as c:
        resp = await c.post(
            "/api/auth/users",
            json={"username": "newbie", "password": "pw123456", "role": "agent", "display_name": "新"},
        )
    assert resp.status_code == 201
    assert resp.json()["username"] == "newbie"
    assert len(db.added) == 1


async def test_update_user(app: FastAPI, setup_state) -> None:
    """更新用户"""
    db, _ = setup_state
    db.one = _FakeUser(username="u1")
    async with await _client(app) as c:
        resp = await c.put(
            "/api/auth/users/11111111-2222-3333-4444-555555555555",
            json={"display_name": "改名"},
        )
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "改名"


async def test_delete_user(app: FastAPI, setup_state) -> None:
    """删除用户"""
    db, _ = setup_state
    db.one = _FakeUser(username="u1")
    async with await _client(app) as c:
        resp = await c.delete("/api/auth/users/11111111-2222-3333-4444-555555555555")
    assert resp.status_code == 200
    assert resp.json()["result"] == "ok"


# ── 敏感词 / 规则 / 统计 / 死信 ──


async def test_get_sensitive_words(app: FastAPI, setup_state) -> None:
    """敏感词列表"""
    async with await _client(app) as c:
        resp = await c.get("/api/admin/sensitive-words")
    assert resp.status_code == 200
    assert "保本保息" in resp.json()["words"]


async def test_update_sensitive_words(app: FastAPI, setup_state) -> None:
    """更新敏感词 + 通知 redis"""
    _, redis = setup_state
    async with await _client(app) as c:
        resp = await c.put("/api/admin/sensitive-words", json={"words": ["保证收益", "零风险"]})
    assert resp.status_code == 200
    assert resp.json()["count"] == 2
    assert redis.publish.await_count >= 1


async def test_reload_rules(app: FastAPI, setup_state) -> None:
    """规则热加载通知"""
    async with await _client(app) as c:
        resp = await c.post("/api/admin/rules/reload")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_get_stats(app: FastAPI, setup_state) -> None:
    """业务统计"""
    async with await _client(app) as c:
        resp = await c.get("/api/admin/stats")
    assert resp.status_code == 200
    assert "sessions" in resp.json()


async def test_get_dead_letters(app: FastAPI, setup_state) -> None:
    """死信列表"""
    async with await _client(app) as c:
        resp = await c.get("/api/admin/dead-letter")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


async def test_replay_dead_letter_no_redis(app: FastAPI, setup_state) -> None:
    """无 Redis → 不可重放"""
    app.state.redis_client = None
    async with await _client(app) as c:
        resp = await c.post("/api/admin/dead-letter/replay", json={"message_id": "m1"})
    assert resp.status_code == 200
    assert resp.json()["replayed"] is False


async def test_get_me(app: FastAPI, setup_state) -> None:
    """当前用户信息"""
    async with await _client(app) as c:
        resp = await c.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"
