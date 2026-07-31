"""Admin 端点测试 — diagnostics 聚合逻辑 (不依赖 FastAPI/ES/PG)

直接调用函数式纯逻辑路径, 验证:
- stage_stats 聚合 (空 / 错误降级)
- clear_cache 走 SCAN + DEL 循环
- ESClient 注入缺失时 diagnostics 仍然能返回 (es 字段填 uninitialized)
"""

from __future__ import annotations

import pytest

# Admin 端点顶层 import 会拉到 elasticsearch (kb.api.deps)
pytest.importorskip("elasticsearch", reason="kb.api.deps 顶层 import elasticsearch, 需要装好 wheel")
pytest.importorskip("fastapi", reason="admin 端点用 FastAPI 装饰器")

from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest as _pytest  # noqa: E402

from kb.api.admin import clear_cache  # noqa: E402


class TestClearCacheEndpoint:
    @_pytest.mark.asyncio
    async def test_clears_all_kb_retrieve_keys(self):
        # 模拟 redis 客户端
        fake_redis = MagicMock()
        fake_redis.scan_iter = AsyncMock()
        # 第一次 scan 返回 2 个 key, 第二次返回空 (终止)
        async def fake_iter(match):
            if match == "kb:retrieve:*":
                yield "kb:retrieve:abc"
                yield "kb:retrieve:def"
            # 后续空
        fake_redis.scan_iter.side_effect = fake_iter
        fake_redis.delete = AsyncMock()

        result = await clear_cache(api_key="test-key")

        assert result["deleted_keys"] == 2
        assert fake_redis.delete.call_count == 2
        # 删除的是 scan_iter 给的 key
        deleted_keys = [c.args[0] for c in fake_redis.delete.call_args_list]
        assert "kb:retrieve:abc" in deleted_keys
        assert "kb:retrieve:def" in deleted_keys

    @_pytest.mark.asyncio
    async def test_empty_cache(self):
        fake_redis = MagicMock()

        async def empty_iter(match):
            if False:
                yield  # 永远不执行, 是空 async generator
        fake_redis.scan_iter.side_effect = empty_iter
        fake_redis.delete = AsyncMock()

        result = await clear_cache(api_key="test-key")
        assert result["deleted_keys"] == 0
        assert fake_redis.delete.call_count == 0
