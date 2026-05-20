"""全局异常处理中间件

将 SmartCSError 体系映射为统一的 JSON 错误响应。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from smartcs.shared.config import get_settings
from smartcs.shared.exceptions import SmartCSError

_logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器"""

    @app.exception_handler(SmartCSError)
    async def smartcs_error_handler(request: Request, exc: SmartCSError) -> JSONResponse:
        """处理所有 SmartCSError 子类异常"""
        status_code = 500
        if 2000 <= exc.code < 3000:
            status_code = 400
        elif 3000 <= exc.code < 4000:
            status_code = 422
        elif 4000 <= exc.code < 5000:
            status_code = 502
        else:
            _logger.warning("未识别的错误码范围: %d", exc.code)

        return JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "type": type(exc).__name__,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        """处理请求校验异常，返回统一错误格式"""
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": 2000,
                    "message": "请求参数校验失败",
                    "type": "RequestValidationError",
                    "details": exc.errors(),
                }
            },
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
        """兜底处理未捕获异常"""
        settings = get_settings()
        # 生产环境不暴露内部异常类型名，防止信息泄露
        exc_type = type(exc).__name__ if settings.environment == "development" else "InternalError"
        _logger.exception("未捕获异常: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": 5000,
                    "message": "系统内部错误",
                    "type": exc_type,
                }
            },
        )
