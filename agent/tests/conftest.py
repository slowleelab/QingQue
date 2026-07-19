"""pytest 配置和公共 fixtures — 端到端 API 测试

启动真实 uvicorn 服务器（bot :8000 + assist :8001），
通过 HTTP 请求验证完整请求/响应生命周期。

前置条件：Docker 中间件已启动（make up）
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import AsyncGenerator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

# ── 服务器子进程管理 ──

_bot_process: subprocess.Popen | None = None
_assist_process: subprocess.Popen | None = None

BOT_PORT = 8765  # 避免与开发环境 :8000 冲突
ASSIST_PORT = 8766  # 避免与开发环境 :8001 冲突
AGENT_DIR = Path(__file__).resolve().parent.parent  # agent/ 目录


def _check_port(port: int) -> bool:
    """检查端口是否已被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_port(port: int, timeout: float = 30.0) -> bool:
    """等待端口变为可用"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _check_port(port):
            # 额外检查服务是否已就绪（health endpoint）
            try:
                resp = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=2.0)
                if resp.status_code == 200:
                    return True
            except Exception:
                pass
        time.sleep(0.5)
    return False


def _start_server(service: str, port: int) -> subprocess.Popen:
    """启动 uvicorn 子进程"""
    env = os.environ.copy()
    env["SMARTCS_ENVIRONMENT"] = "development"

    if service == "bot":
        target = "smartcs.main:bot_app"
    else:
        target = "smartcs.main:assist_app"

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        target,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]

    proc = subprocess.Popen(
        cmd,
        cwd=str(AGENT_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc


def _stop_server(proc: subprocess.Popen | None, name: str):
    """停止服务器子进程"""
    if proc is None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    except Exception:
        pass


def _check_middleware_ready() -> bool:
    """快速检查核心中间件端口是否可达"""
    for host, port in [("127.0.0.1", 6379), ("127.0.0.1", 5432)]:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                if s.connect_ex((host, port)) != 0:
                    return False
        except Exception:
            return False
    return True


# ── Fixtures ──


@pytest.fixture(scope="session")
def bot_server():
    """Session-scoped: 启动机器人服务 uvicorn 子进程"""
    global _bot_process

    if not _check_middleware_ready():
        pytest.skip("Docker 中间件未启动，请运行 make up")

    if _check_port(BOT_PORT):
        # 端口已被占用，可能已有实例在运行，直接复用
        pytest.skip(f"端口 {BOT_PORT} 已被占用，跳过启动")

    _bot_process = _start_server("bot", BOT_PORT)

    if not _wait_for_port(BOT_PORT, timeout=90):
        _stop_server(_bot_process, "bot")
        pytest.fail(f"Bot 服务启动超时（端口 {BOT_PORT} 未就绪）")

    yield f"http://127.0.0.1:{BOT_PORT}"

    _stop_server(_bot_process, "bot")
    _bot_process = None


@pytest.fixture(scope="session")
def assist_server():
    """Session-scoped: 启动坐席辅助服务 uvicorn 子进程"""
    global _assist_process

    if not _check_middleware_ready():
        pytest.skip("Docker 中间件未启动，请运行 make up")

    if _check_port(ASSIST_PORT):
        pytest.skip(f"端口 {ASSIST_PORT} 已被占用，跳过启动")

    _assist_process = _start_server("assist", ASSIST_PORT)

    if not _wait_for_port(ASSIST_PORT, timeout=90):
        _stop_server(_assist_process, "assist")
        pytest.fail(f"Assist 服务启动超时（端口 {ASSIST_PORT} 未就绪）")

    yield f"http://127.0.0.1:{ASSIST_PORT}"

    _stop_server(_assist_process, "assist")
    _assist_process = None


@pytest_asyncio.fixture
async def bot_client(bot_server: str) -> AsyncGenerator[httpx.AsyncClient, None]:
    """机器人服务 HTTP 测试客户端"""
    async with httpx.AsyncClient(base_url=bot_server, timeout=30.0) as client:
        yield client


@pytest_asyncio.fixture
async def assist_client(assist_server: str) -> AsyncGenerator[httpx.AsyncClient, None]:
    """坐席辅助服务 HTTP 测试客户端"""
    async with httpx.AsyncClient(base_url=assist_server, timeout=30.0) as client:
        yield client
