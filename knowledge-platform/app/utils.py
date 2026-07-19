"""工具函数 — 无重依赖，可安全在测试中导入"""

from __future__ import annotations

import os


def sanitize_filename(filename: str) -> str:
    """安全化文件名，防止路径穿越

    - 只保留 basename
    - 替换危险字符 (.., /, \\)
    - 限制长度
    """
    safe = os.path.basename(filename)
    safe = safe.replace("..", "").replace("/", "_").replace("\\", "_")
    if len(safe) > 200:
        name, ext = os.path.splitext(safe)
        safe = name[:200 - len(ext)] + ext
    return safe
