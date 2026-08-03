"""gRPC 客户端契约 (P1-2A 整改: 当前未部署, 仅作未来接口)

历史: 此模块为 AI 能力层 (Classification/Retrieval/Safety) gRPC 客户端
工厂, 由 FastAPI app.state 管理连接通道, 配合 deps.py 依赖注入.

现状 (2026-08):
- 编排层当前使用本地实现 (classifier.py / retrieval.py / safety.py),
  0 caller 调 get_classification_stub / get_retrieval_stub / get_safety_stub
- init_grpc_channels / close_grpc_channels 已从 main.py 移除 (P1-2A)
- proto 文件 + 生成 stub 仍保留 (agent/proto/, agent/generated/proto/),
  供未来 gRPC 部署时直接使用

未来真接 gRPC 时的步骤:
1. 在 _BOT_INIT_STEPS / _ASSIST_INIT_STEPS 加回 init_grpc_channels
2. 在 deps.py 切换本地实现 → get_*_stub 注入
3. 启动 gRPC server (mcp-server 或独立 ai-capability-svc)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def init_grpc_channels(app: FastAPI) -> None:
    """占位: 未来启动 gRPC 通道时实现. 当前 P1-2A 整改后未在 main.py 调用.

    历史行为: 创建 3 个 insecure_channel (classification/retrieval/safety),
    存到 app.state.grpc_channels. 现在保留函数签名为未来真接时兼容.
    """
    app.state.grpc_channels = {}


def close_grpc_channels(app: FastAPI) -> None:
    """占位: 未来关闭 gRPC 通道时实现."""
    app.state.grpc_channels = None


def get_classification_stub(app: FastAPI):
    """占位: 未来 gRPC 部署时返回 classification_pb2_grpc.ClassificationServiceStub.

    当前 0 caller. 真接时需先 init_grpc_channels 建连再调本函数.
    """
    raise NotImplementedError(
        "gRPC 通道未初始化 (P1-2A 整改后). "
        "如需启用, 在 _BOT_INIT_STEPS 加回 init_grpc_channels 并启动 AI 能力层 gRPC server."
    )


def get_retrieval_stub(app: FastAPI):
    """占位: 未来 gRPC 部署时返回 retrieval_pb2_grpc.RetrievalServiceStub."""
    raise NotImplementedError(
        "gRPC 通道未初始化 (P1-2A 整改后). "
        "如需启用, 在 _BOT_INIT_STEPS 加回 init_grpc_channels 并启动 AI 能力层 gRPC server."
    )


def get_safety_stub(app: FastAPI):
    """占位: 未来 gRPC 部署时返回 safety_pb2_grpc.SafetyFilterServiceStub."""
    raise NotImplementedError(
        "gRPC 通道未初始化 (P1-2A 整改后). "
        "如需启用, 在 _BOT_INIT_STEPS 加回 init_grpc_channels 并启动 AI 能力层 gRPC server."
    )
