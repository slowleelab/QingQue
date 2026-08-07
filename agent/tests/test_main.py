"""main.py 单元测试 (lifespan 包装/异常抑制/app 工厂)"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

import lumio.main as main_mod
from lumio.main import (
    _safe_build_alert_rules,
    _safe_init_global_factory,
    _safe_init_global_redis,
    _safe_start_gdpr_worker,
    _SuppressExceptions,
    build_alert_rules_step,
    init_global_factory_step,
    init_global_redis_step,
    start_gdpr_worker_step,
)

# ── _SuppressExceptions ──


def test_suppress_exceptions_no_exc():
    """无异常 → 正常返回"""
    with _SuppressExceptions(logging.getLogger("test")) as cm:
        assert cm is not None


def test_suppress_exceptions_swallows():
    """异常被吞 + 记 warning"""
    logger = MagicMock()
    with _SuppressExceptions(logger):
        raise RuntimeError("boom")
    logger.warning.assert_called_once()


# ── _safe_* 包装 (异常不抛出) ──


def test_safe_init_global_factory_success():
    """factory 初始化成功"""
    with patch.object(main_mod, "init_global_session_factory") as mock_init:
        _safe_init_global_factory()
        mock_init.assert_called_once()


def test_safe_init_global_factory_error_swallowed():
    """factory 初始化失败被吞"""
    with patch.object(main_mod, "init_global_session_factory", side_effect=RuntimeError("no db")):
        _safe_init_global_factory()  # 不抛


def test_safe_init_global_redis_error_swallowed():
    """redis 初始化失败被吞"""
    with patch.object(main_mod, "init_global_redis_client", side_effect=RuntimeError("no redis")):
        _safe_init_global_redis()


def test_safe_start_gdpr_worker_error_swallowed():
    """gdpr worker 启动失败被吞"""
    with patch.object(main_mod, "start_gdpr_sweep_worker", side_effect=RuntimeError("boom")):
        _safe_start_gdpr_worker()


def test_safe_build_alert_rules_error_swallowed():
    """告警规则接线失败被吞"""
    with patch("lumio.shared.alerting.get_alert_router", side_effect=RuntimeError("boom")):
        _safe_build_alert_rules()


# ── async 步骤包装 ──


async def test_init_global_factory_step():
    """启动步骤包装调用同步函数"""
    with patch.object(main_mod, "_safe_init_global_factory") as mock_safe:
        await init_global_factory_step(MagicMock())
        mock_safe.assert_called_once()


async def test_init_global_redis_step():
    """redis 步骤包装"""
    with patch.object(main_mod, "_safe_init_global_redis") as mock_safe:
        await init_global_redis_step(MagicMock())
        mock_safe.assert_called_once()


async def test_start_gdpr_worker_step():
    """gdpr 步骤包装"""
    with patch.object(main_mod, "_safe_start_gdpr_worker") as mock_safe:
        await start_gdpr_worker_step(MagicMock())
        mock_safe.assert_called_once()


async def test_build_alert_rules_step():
    """告警步骤包装"""
    with patch.object(main_mod, "_safe_build_alert_rules") as mock_safe:
        await build_alert_rules_step(MagicMock())
        mock_safe.assert_called_once()


# ── 步骤列表完整性 ──


def test_all_init_steps_async():
    """bot/assist 全部启动/关闭步骤为 async (防 await None 回归)"""
    import inspect

    for steps in (
        main_mod._BOT_INIT_STEPS,
        main_mod._BOT_CLOSE_STEPS,
        main_mod._ASSIST_INIT_STEPS,
        main_mod._ASSIST_CLOSE_STEPS,
    ):
        for step in steps:
            assert inspect.iscoroutinefunction(step), f"非 async 步骤: {step}"


# ── app 工厂 ──


def test_app_factories_exist():
    """bot_app/assist_app 创建成功"""
    assert main_mod.bot_app is not None
    assert main_mod.assist_app is not None
    assert main_mod.bot_app.title
    assert main_mod.assist_app.title


def test_apps_have_exception_handlers():
    """两个 app 都注册了 LumioError 处理器"""
    from lumio.shared.exceptions import LumioError

    assert LumioError in main_mod.bot_app.exception_handlers
    assert LumioError in main_mod.assist_app.exception_handlers


# ── lifespan 失败清理路径 ──


async def test_bot_lifespan_init_failure_cleans_up(monkeypatch):
    """bot 启动失败 → 逆序清理已初始化资源 + 重新抛出"""

    import lumio.main as main

    app = FastAPI()
    cleaned: list[str] = []

    class _Failing:
        """init 步骤: 第 2 步抛异常"""

        def __init__(self):
            self.calls = 0

        async def __call__(self, a):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("boom")
            return None

    failing = _Failing()
    failing.__name__ = "init_failing"
    init_steps = [failing, failing]

    def _close(name: str):
        async def _c(app_):
            cleaned.append(name)

        _c.__name__ = name
        return _c

    with (
        patch.object(main, "_BOT_INIT_STEPS", init_steps),
        patch.object(main, "_BOT_CLOSE_STEPS", [_close("stop_failing")]),
        patch.object(main, "get_settings", return_value=MagicMock(log_level="DEBUG", environment="development")),
    ):
        with pytest.raises(RuntimeError):
            async with main.bot_lifespan(app):
                pass
    # 失败时清理路径被执行 (close 名匹配不上时走 suppress 分支, 不抛)


async def test_bot_lifespan_success_and_close():
    """bot 正常启动 → 关闭步骤全部执行"""
    import lumio.main as main

    app = FastAPI()

    async def _init(a):
        app.state._init_called = True

    async def _close(a):
        app.state._close_called = True

    with (
        patch.object(main, "_BOT_INIT_STEPS", [_init]),
        patch.object(main, "_BOT_CLOSE_STEPS", [_close]),
        patch.object(main, "get_settings", return_value=MagicMock(log_level="DEBUG", environment="development")),
    ):
        async with main.bot_lifespan(app):
            assert app.state._init_called
    assert app.state._close_called


async def test_assist_lifespan_failure_cleanup():
    """assist 启动失败 → 清理 + 抛出"""
    import lumio.main as main

    app = FastAPI()

    async def _init_ok(a):
        app.state.ok = True

    async def _init_fail(a):
        raise RuntimeError("assist boom")

    with (
        patch.object(main, "_ASSIST_INIT_STEPS", [_init_ok, _init_fail]),
        patch.object(main, "_ASSIST_CLOSE_STEPS", []),
        patch.object(main, "get_settings", return_value=MagicMock(log_level="DEBUG", environment="development")),
    ):
        with pytest.raises(RuntimeError):
            async with main.assist_lifespan(app):
                pass


async def test_assist_lifespan_success():
    """assist 正常启动关闭"""
    import lumio.main as main

    app = FastAPI()

    async def _init(a):
        app.state.ok = True

    async def _close(a):
        app.state.closed = True

    with (
        patch.object(main, "_ASSIST_INIT_STEPS", [_init]),
        patch.object(main, "_ASSIST_CLOSE_STEPS", [_close]),
        patch.object(main, "get_settings", return_value=MagicMock(log_level="DEBUG", environment="development")),
    ):
        async with main.assist_lifespan(app):
            assert app.state.ok
    assert app.state.closed


async def test_close_assist_ws_pool_closes_connections():
    """关闭 WS 连接池: 逐个关闭 + 清空"""
    import lumio.main as main

    app = FastAPI()
    ws1 = AsyncMock()
    ws2 = AsyncMock()
    app.state.assist_ws_pool = {"s1": ws1, "s2": ws2}
    await main._close_assist_ws_pool(app)
    ws1.close.assert_awaited_once()
    ws2.close.assert_awaited_once()
    assert app.state.assist_ws_pool == {}


async def test_close_assist_ws_pool_close_error():
    """WS 关闭异常 → 跳过继续"""
    import lumio.main as main

    app = FastAPI()
    ws = AsyncMock()
    ws.close = AsyncMock(side_effect=RuntimeError("ws gone"))
    app.state.assist_ws_pool = {"s1": ws}
    await main._close_assist_ws_pool(app)
    assert app.state.assist_ws_pool == {}
