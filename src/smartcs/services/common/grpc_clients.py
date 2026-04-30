"""gRPC 客户端工厂

为编排层提供统一的 gRPC stub 创建接口。
AI 能力层（分类/检索/安全过滤）通过 gRPC 通信。
使用 FastAPI app.state 管理连接通道，支持依赖注入。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import grpc

from smartcs.shared.config import get_settings

if TYPE_CHECKING:
    from fastapi import FastAPI


async def init_grpc_channels(app: FastAPI) -> None:
    """初始化 gRPC 通道，存储到 app.state"""
    settings = get_settings()
    channels: dict[str, grpc.aio.Channel] = {}

    cls_target = f"{settings.classification.grpc_host}:{settings.classification.grpc_port}"
    rag_target = f"{settings.rag.grpc_host}:{settings.rag.grpc_port}"
    safety_target = f"{settings.safety.grpc_host}:{settings.safety.grpc_port}"

    for target in {cls_target, rag_target, safety_target}:
        channels[target] = grpc.aio.insecure_channel(target)

    app.state.grpc_channels = channels


async def close_grpc_channels(app: FastAPI) -> None:
    """关闭所有 gRPC 通道"""
    channels: dict[str, grpc.aio.Channel] = getattr(app.state, "grpc_channels", {})
    for channel in channels.values():
        await channel.close()
    channels.clear()
    app.state.grpc_channels = {}


def get_classification_stub(app: FastAPI):
    """获取分类模型服务 stub"""
    from generated.proto import classification_pb2_grpc

    settings = get_settings()
    target = f"{settings.classification.grpc_host}:{settings.classification.grpc_port}"
    channel = app.state.grpc_channels[target]
    return classification_pb2_grpc.ClassificationServiceStub(channel)


def get_retrieval_stub(app: FastAPI):
    """获取 RAG 检索服务 stub"""
    from generated.proto import retrieval_pb2_grpc

    settings = get_settings()
    target = f"{settings.rag.grpc_host}:{settings.rag.grpc_port}"
    channel = app.state.grpc_channels[target]
    return retrieval_pb2_grpc.RetrievalServiceStub(channel)


def get_safety_stub(app: FastAPI):
    """获取安全过滤服务 stub"""
    from generated.proto import safety_pb2_grpc

    settings = get_settings()
    target = f"{settings.safety.grpc_host}:{settings.safety.grpc_port}"
    channel = app.state.grpc_channels[target]
    return safety_pb2_grpc.SafetyFilterServiceStub(channel)
