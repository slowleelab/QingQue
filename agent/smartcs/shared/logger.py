"""统一日志配置

支持标准文本格式和 JSON 结构化格式（生产环境推荐）。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime


class JSONFormatter(logging.Formatter):
    """JSON 结构化日志格式器"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        # 合并 extra 字段（如 logger.info("msg", extra={"request_id": "..."})）
        if hasattr(record, "extra") and isinstance(record.extra, dict):
            log_entry.update(record.extra)
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logger(name: str, level: str = "INFO", *, json_format: bool = False) -> logging.Logger:
    """创建标准化 logger

    Args:
        name: logger 名称
        level: 日志级别
        json_format: 是否使用 JSON 格式输出（生产环境推荐）
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level_value = getattr(logging, level.upper(), None)
    if level_value is None:
        import warnings

        warnings.warn(f"无效的日志级别 '{level}'，回退到 INFO", stacklevel=2)
        level_value = logging.INFO
    logger.setLevel(level_value)

    handler = logging.StreamHandler(sys.stdout)
    if json_format:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
    logger.addHandler(handler)
    return logger
