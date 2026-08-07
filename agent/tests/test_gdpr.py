"""GDPR 客户数据删除单元测试 (gdpr.py)"""

from __future__ import annotations

import asyncio
import json

import pytest

from lumio.services.common.gdpr import (
    DeletionRequest,
    DeletionStatus,
    GDPRService,
    _gdpr_sweep_loop,
    get_gdpr_service,
    start_gdpr_sweep_worker,
    stop_gdpr_sweep_worker,
)


def test_deletion_status_values():
    """4 种删除状态"""
    assert DeletionStatus.REQUESTED.value == "requested"
    assert DeletionStatus.SOFT_DELETED.value == "soft_deleted"
    assert DeletionStatus.HARD_DELETED.value == "hard_deleted"
    assert DeletionStatus.FAILED.value == "failed"


def test_deletion_request_to_dict():
    """请求记录序列化"""
    req = DeletionRequest(
        request_id="r1",
        customer_id="c1",
        requested_at=1.0,
        soft_deleted_at=2.0,
        status=DeletionStatus.SOFT_DELETED,
        affected_sessions=3,
        failed_layers=["redis"],
    )
    data = req.to_dict()
    assert data["request_id"] == "r1"
    assert data["status"] == "soft_deleted"
    assert data["affected_sessions"] == 3
    assert data["failed_layers"] == ["redis"]


class _FakeRedis:
    """异步 Redis mock"""

    def __init__(self) -> None:
        self.data: dict[str, dict] = {}
        self.calls: list[tuple] = []

    async def hset(self, key: str, mapping: dict) -> None:
        self.data[key] = {**self.data.get(key, {}), **mapping}
        self.calls.append(("hset", key))

    async def expire(self, key: str, ttl: int) -> None:
        self.calls.append(("expire", key, ttl))

    async def zadd(self, key: str, mapping: dict) -> None:
        self.calls.append(("zadd", key, mapping))

    async def hgetall(self, key: str) -> dict:
        return self.data.get(key, {})

    async def get(self, key: str) -> str | None:
        return self.data.get(key, {}).get("_raw")

    async def delete(self, *keys: str) -> int:
        for k in keys:
            self.data.pop(k, None)
        return len(keys)

    async def zrangebyscore(self, key: str, mn: float, mx: float, start: int, num: int):
        return [r for r, s in sorted(self._z.get(key, {}).items(), key=lambda kv: kv[1]) if mn <= s <= mx][
            start : start + num
        ]

    async def zrem(self, key: str, rid: str) -> None:
        self._z.get(key, {}).pop(rid, None)

    async def scan_iter(self, match: str, count: int):
        for k in list(self.data.keys()):
            if k.startswith(match.replace("*", "")) or "*" in match:
                yield k

    def _set_raw(self, key: str, value: str) -> None:
        self.data[key] = {"_raw": value}


# ── request_deletion ──


async def test_request_deletion_success():
    """申请删除: Redis 记录 + 调度 + 软删除会话"""
    service = GDPRService()
    fake = _FakeRedis()
    service._redis = fake

    # 活跃 session 清理返回 0 (无匹配)
    req = await service.request_deletion("c1")

    assert req.status == DeletionStatus.SOFT_DELETED
    assert req.affected_sessions == 0
    keys = [c[0] for c in fake.calls]
    assert "hset" in keys and "expire" in keys and "zadd" in keys
    # 调度分数 = 现在 + 30 天
    zadd_call = next(c for c in fake.calls if c[0] == "zadd")
    score = next(iter(zadd_call[2].values()))
    assert score > 30 * 86400


async def test_request_deletion_with_rid():
    """传入 request_id 时复用"""
    service = GDPRService()
    service._redis = _FakeRedis()
    req = await service.request_deletion("c1", request_id="custom-rid")
    assert req.request_id == "custom-rid"


async def test_request_deletion_no_redis(monkeypatch):
    """无 Redis 时降级: 记录失败但软删除仍完成"""
    import lumio.services.common.redis_client as rc

    def boom():
        raise RuntimeError("no redis")

    monkeypatch.setattr(rc, "get_redis_client", boom)
    service = GDPRService()
    service._redis = None
    req = await service.request_deletion("c1")
    assert req.status == DeletionStatus.SOFT_DELETED  # _cleanup 返回 0 不抛


async def test_request_deletion_redis_boom():
    """Redis 记录异常被吞, 不阻断"""
    service = GDPRService()

    class _Boom:
        async def hset(self, *a):
            raise RuntimeError("down")

        async def expire(self, *a):
            raise RuntimeError("down")

        async def zadd(self, *a):
            raise RuntimeError("down")

        async def scan_iter(self, match: str, count: int):
            if False:
                yield

        async def get(self, key: str):
            return None

        async def delete(self, *keys: str):
            return 0

    service._redis = _Boom()
    req = await service.request_deletion("c1")
    assert req.status == DeletionStatus.SOFT_DELETED


async def test_request_deletion_cleanup_fails():
    """会话清理失败 → FAILED + failed_layers"""
    service = GDPRService()
    service._redis = _FakeRedis()

    async def boom(customer_id: str) -> int:
        raise RuntimeError("cleanup failed")

    service._cleanup_active_sessions = boom  # type: ignore[method-assign]
    req = await service.request_deletion("c1")
    assert req.status == DeletionStatus.FAILED
    assert "redis_sessions" in req.failed_layers


# ── hard_delete ──


def _make_hard_delete_service() -> tuple[GDPRService, _FakeRedis]:
    service = GDPRService()
    fake = _FakeRedis()
    fake.data["lumio:gdpr:deletion:r1"] = {
        "customer_id": "c1",
        "requested_at": "1000.0",
        "status": "soft_deleted",
    }
    service._redis = fake
    return service, fake


async def test_hard_delete_success():
    """硬删除全链路成功 → HARD_DELETED"""
    service, fake = _make_hard_delete_service()

    async def fake_pg(cid: str) -> int:
        return 5

    service._delete_postgres = fake_pg  # type: ignore[method-assign]
    service._delete_milvus = lambda cid: asyncio.sleep(0, result=0)  # type: ignore[method-assign]
    service._delete_elasticsearch = lambda cid: asyncio.sleep(0, result=0)  # type: ignore[method-assign]
    service._delete_redis_by_customer = lambda cid: asyncio.sleep(0, result=2)  # type: ignore[method-assign]

    req = await service.hard_delete("r1")
    assert req.status == DeletionStatus.HARD_DELETED
    assert req.affected_logs == 5
    assert req.affected_sessions == 2
    assert req.hard_deleted_at is not None
    # 状态回写 Redis
    assert fake.data["lumio:gdpr:deletion:r1"]["status"] == "hard_deleted"


async def test_hard_delete_missing_request():
    """请求不存在 → ValueError"""
    service, _ = _make_hard_delete_service()
    with pytest.raises(ValueError, match="删除请求不存在"):
        await service.hard_delete("no-such")


async def test_hard_delete_no_redis(monkeypatch):
    """Redis 不可用 → RuntimeError"""
    import lumio.services.common.redis_client as rc

    def boom():
        raise RuntimeError("no redis")

    monkeypatch.setattr(rc, "get_redis_client", boom)
    service = GDPRService()
    with pytest.raises(RuntimeError, match="Redis 不可用"):
        await service.hard_delete("r1")


async def test_hard_delete_partial_failure():
    """部分层失败 → FAILED + failed_layers"""
    service, fake = _make_hard_delete_service()

    async def boom_pg(cid: str) -> int:
        raise RuntimeError("pg down")

    service._delete_postgres = boom_pg  # type: ignore[method-assign]
    service._delete_milvus = lambda cid: asyncio.sleep(0, result=0)  # type: ignore[method-assign]
    service._delete_elasticsearch = lambda cid: asyncio.sleep(0, result=0)  # type: ignore[method-assign]
    service._delete_redis_by_customer = lambda cid: asyncio.sleep(0, result=0)  # type: ignore[method-assign]

    req = await service.hard_delete("r1")
    assert req.status == DeletionStatus.FAILED
    assert "postgres" in req.failed_layers
    assert fake.data["lumio:gdpr:deletion:r1"]["failed_layers"] == "postgres"


# ── get_status ──


async def test_get_status_found():
    """查询存在的请求"""
    service, fake = _make_hard_delete_service()
    req = await service.get_status("r1")
    assert req is not None
    assert req.customer_id == "c1"
    assert req.requested_at == 1000.0


async def test_get_status_missing():
    """查询不存在的请求 → None"""
    service, _ = _make_hard_delete_service()
    assert await service.get_status("nope") is None


async def test_get_status_no_redis(monkeypatch):
    """无 Redis → None"""
    import lumio.services.common.redis_client as rc

    def boom():
        raise RuntimeError("no redis")

    monkeypatch.setattr(rc, "get_redis_client", boom)
    service = GDPRService()
    assert await service.get_status("r1") is None


# ── 内部清理方法 ──


async def test_delete_redis_by_customer_match():
    """SCAN 匹配 customer_id 的 session 删除 (meta+history+slot)"""
    service = GDPRService()
    fake = _FakeRedis()
    fake._set_raw("lumio:session:s1:meta", json.dumps({"customer_id": "c1", "session_id": "s1"}))
    fake._set_raw("lumio:session:s2:meta", json.dumps({"customer_id": "c2", "session_id": "s2"}))
    fake._set_raw("lumio:session:s1:history", json.dumps(["msg"]))
    fake._set_raw("lumio:slot:s1", "{}")
    service._redis = fake

    deleted = await service._delete_redis_by_customer("c1")
    assert deleted == 1
    assert "lumio:session:s1:meta" not in fake.data
    assert "lumio:session:s1:history" not in fake.data
    assert "lumio:slot:s1" not in fake.data
    assert "lumio:session:s2:meta" in fake.data  # 其他客户保留


async def test_delete_redis_by_customer_bad_json():
    """坏 JSON 的 meta 跳过"""
    service = GDPRService()
    fake = _FakeRedis()
    fake._set_raw("lumio:session:s1:meta", "not-json{{{")
    service._redis = fake
    assert await service._delete_redis_by_customer("c1") == 0


async def test_delete_redis_by_customer_no_redis(monkeypatch):
    """无 Redis → 0"""
    import lumio.services.common.redis_client as rc

    def boom():
        raise RuntimeError("no redis")

    monkeypatch.setattr(rc, "get_redis_client", boom)
    service = GDPRService()
    assert await service._delete_redis_by_customer("c1") == 0


class _FakeResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakePgSession:
    def __init__(self) -> None:
        self.executes: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, stmt):
        self.executes.append(str(stmt)[:40])
        return _FakeResult(2)

    async def commit(self) -> None:
        pass


async def test_delete_postgres_injected_factory():
    """已注入 db factory 时正常删除三张表"""
    service = GDPRService()
    service._db_session_factory = lambda: _FakePgSession()
    deleted = await service._delete_postgres("c1")
    assert deleted == 6  # 3 张表 × rowcount 2


async def test_delete_postgres_no_factory(monkeypatch):
    """无 factory 时从全局懒加载 (失败降级为抛出, 由调用方捕获)"""
    import lumio.services.common.database as db_mod

    def boom():
        raise RuntimeError("no db")

    monkeypatch.setattr(db_mod, "get_async_session_factory", boom)
    service = GDPRService()
    service._redis = _FakeRedis()
    with pytest.raises(RuntimeError):
        await service._delete_postgres("c1")


async def test_delete_milvus_not_configured():
    """Milvus collection 未配置 → 跳过返回 0"""
    service = GDPRService()
    assert await service._delete_milvus("c1") == 0


async def test_delete_milvus_configured():
    """配置 collection → 返回删除数"""
    service = GDPRService()

    class _FakeCollection:
        def delete(self, expr: str):
            return type("R", (), {"delete_count": 3})()

    service._milvus_collection = _FakeCollection()  # type: ignore[attr-defined]
    assert await service._delete_milvus("c1") == 3


async def test_delete_milvus_error_best_effort():
    """Milvus 异常 → 0 (不阻断)"""
    service = GDPRService()

    class _BoomCollection:
        def delete(self, expr: str):
            raise RuntimeError("milvus down")

    service._milvus_collection = _BoomCollection()  # type: ignore[attr-defined]
    assert await service._delete_milvus("c1") == 0


async def test_delete_es_not_configured():
    """ES client 未配置 → 跳过返回 0"""
    service = GDPRService()
    assert await service._delete_elasticsearch("c1") == 0


async def test_delete_es_configured():
    """配置 ES → 删两个索引"""
    service = GDPRService()

    class _FakeES:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def delete_by_query(self, index: str, body: dict, **kw):
            self.calls.append(index)
            return {"deleted": 2}

    es = _FakeES()
    service._es_client = es  # type: ignore[attr-defined]
    deleted = await service._delete_elasticsearch("c1")
    assert deleted == 4
    assert len(es.calls) == 2


async def test_delete_es_index_error_skips():
    """单个索引异常跳过, 其余继续"""
    service = GDPRService()

    class _FakeES:
        def __init__(self) -> None:
            self.n = 0

        async def delete_by_query(self, index: str, body: dict, **kw):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("index not found")
            return {"deleted": 1}

    service._es_client = _FakeES()  # type: ignore[attr-defined]
    assert await service._delete_elasticsearch("c1") == 1


# ── redis 获取与单例 ──


async def test_get_redis_failure_cached(monkeypatch):
    """Redis 初始化失败后缓存 False"""
    import lumio.services.common.redis_client as rc

    def boom():
        raise RuntimeError("no redis")

    monkeypatch.setattr(rc, "get_redis_client", boom)
    service = GDPRService()
    assert await service._get_redis() is None
    assert service._redis is False


def test_get_gdpr_service_singleton():
    """全局单例"""
    assert get_gdpr_service() is get_gdpr_service()


# ── 后台调度 worker ──


async def test_gdpr_sweep_loop_processing(monkeypatch):
    """到期请求被硬删除并从调度队列移除"""
    import lumio.services.common.gdpr as gdpr_mod

    service = GDPRService()
    fake = _FakeRedis()
    fake.data["lumio:gdpr:deletion:r1"] = {"customer_id": "c1", "requested_at": "1.0"}
    fake._z = {"lumio:gdpr:scheduled": {"r1": 100.0}}
    service._redis = fake

    async def fake_hard_delete(rid: str) -> DeletionRequest:
        return DeletionRequest(request_id=rid, customer_id="c1", requested_at=1.0)

    service.hard_delete = fake_hard_delete  # type: ignore[method-assign]
    monkeypatch.setattr(gdpr_mod, "get_gdpr_service", lambda: service)

    # 跑一轮: zrangebyscore 有到期任务 → 处理 → sleep 前抛 KeyboardInterrupt 退出
    slept = 0

    async def fake_sleep(seconds: float):
        nonlocal slept
        slept += 1
        raise KeyboardInterrupt("stop loop")

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    with pytest.raises(KeyboardInterrupt):
        await _gdpr_sweep_loop()
    assert slept == 1
    assert "r1" not in fake._z["lumio:gdpr:scheduled"]  # 已从队列移除


async def test_gdpr_sweep_loop_error_handling(monkeypatch):
    """hard_delete 失败: 记录错误且不移除队列"""
    import lumio.services.common.gdpr as gdpr_mod

    service = GDPRService()
    fake = _FakeRedis()
    fake.data["lumio:gdpr:deletion:r1"] = {"customer_id": "c1", "requested_at": "1.0"}
    fake._z = {"lumio:gdpr:scheduled": {"r1": 100.0}}
    service._redis = fake

    async def boom(rid: str) -> DeletionRequest:
        raise RuntimeError("delete failed")

    service.hard_delete = boom  # type: ignore[method-assign]
    monkeypatch.setattr(gdpr_mod, "get_gdpr_service", lambda: service)

    async def fake_sleep(seconds: float):
        raise KeyboardInterrupt("stop loop")

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    with pytest.raises(KeyboardInterrupt):
        await _gdpr_sweep_loop()
    assert "r1" in fake._z["lumio:gdpr:scheduled"]  # 保留待重试


async def test_start_stop_worker():
    """start 创建后台 task, stop 取消"""
    start_gdpr_sweep_worker()
    task = _gdpr_sweep_task = __import__("lumio.services.common.gdpr", fromlist=["_gdpr_sweep_task"])._gdpr_sweep_task
    assert task is not None
    assert not task.done()
    stop_gdpr_sweep_worker()
    import lumio.services.common.gdpr as gdpr_mod

    assert gdpr_mod._gdpr_sweep_task is None
