"""编排服务模块"""

__all__ = ["create_assist_app", "create_bot_app"]


def __getattr__(name: str):
    """延迟导入，避免在 import smartcs.services 时触发完整的依赖链"""
    if name == "create_assist_app":
        from smartcs.services.assist.app import create_assist_app

        return create_assist_app
    if name == "create_bot_app":
        from smartcs.services.bot.app import create_bot_app

        return create_bot_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
