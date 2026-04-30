"""全局异常处理中间件

将 SmartCSError 体系映射为统一的 JSON 错误响应。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from smartcs.shared.exceptions import SmartCSError


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

    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
        """兜底处理未捕获异常"""
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": 5000,
                    "message": "系统内部错误",
                    "type": type(exc).__name__,
                }
            },
        )
