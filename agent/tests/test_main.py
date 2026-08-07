"""main.py 单元测试 (lifespan 包装/异常抑制/app 工厂)"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

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
