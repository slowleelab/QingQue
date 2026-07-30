"""智能客服平台 - 入口转发

此文件保留为向后兼容入口，实际逻辑在 lumio.main 中。
启动方式:
    uvicorn main:bot_app --reload --port 8000
    uvicorn main:assist_app --reload --port 8001
"""

from lumio.main import assist_app, bot_app

__all__ = ["assist_app", "bot_app"]
