"""结构化日志配置

使用 structlog 输出 JSON 结构化日志，同时桥接 stdlib logging
使现有 logging.getLogger() 调用也获得结构化输出。

生产级特性：
- JSON 格式输出，便于 ELK/Loki 采集
- contextvars 支持 request/trace 上下文绑定
- 异常堆栈结构化
- stdlib 桥接：无需改动现有 logging.getLogger 代码
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    """配置结构化日志

    Args:
        level: 日志级别字符串 (DEBUG/INFO/WARNING/ERROR)
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # 共享处理器链 — structlog 和 stdlib 桥接共用
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # 配置 structlog（新代码用 structlog.get_logger()）
    structlog.configure(
        processors=shared_processors + [structlog.processors.JSONRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )

    # 桥接 stdlib logging → structlog JSON 输出
    # 现有 logging.getLogger(__name__) 调用自动获得结构化输出
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # 降低第三方库噪声
    for noisy in ("urllib3", "asyncio", "elasticsearch", "aiokafka"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None):
    """获取 structlog logger

    新代码推荐使用：
        from app.logging import get_logger
        logger = get_logger(__name__)
    """
    return structlog.get_logger(name)


def bind_context(**kwargs):
    """绑定日志上下文（如 request_id, doc_id）

    示例:
        bind_context(doc_id="xxx", trace_id="yyy")
        logger.info("processing")  # 自动携带 doc_id, trace_id
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context():
    """清除日志上下文"""
    structlog.contextvars.clear_contextvars()
