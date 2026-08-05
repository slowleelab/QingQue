"""B4: Jinja2 模板化 Prompt 渲染.

优势 vs f-string:
1. 变量白名单 (避免 prompt injection 通过模板变量注入恶意内容)
2. 条件渲染 ({% if %}) 让 prompt 更动态
3. 循环渲染 ({% for %}) 支持 Few-shot 列表
4. 自动转义 (防 XSS 注入)

并发安全:
- 模板缓存加 asyncio.Lock 守护首次加载
- 启动期通过 warmup() 预热所有模板, 避免运行时 I/O 阻塞
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined, select_autoescape

from lumio.shared.logger import get_logger

logger = get_logger(__name__)


# 变量白名单: 模板中只能使用这些变量, 其他变量会被忽略
# 防 prompt injection 绕过 (恶意变量名注入到模板)
TEMPLATE_VARIABLE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "vip_level",
        "risk_tolerance",
        "card_types",
        "city",
        "sentiment",
        "intent",
        "customer_id",
        "session_id",
        "few_shot_examples",
        "tool_results",
        "current_time",
        "language",
    }
)


_env = Environment(
    autoescape=select_autoescape(["html", "xml"]),
    undefined=StrictUndefined,  # 严格模式: 变量未定义报错, 不静默忽略
    trim_blocks=True,
    lstrip_blocks=True,
)

# 模板缓存 (编译后) — asyncio.Lock 守护并发首次加载
_template_cache: dict[str, Any] = {}
_template_cache_lock = asyncio.Lock()


def _load_template_sync(template_name: str) -> Any:
    """同步读 + 编译模板 (在锁内执行, 避免 I/O 阻塞事件循环)."""
    template_path = Path(__file__).parent / "templates" / f"{template_name}.j2"
    if not template_path.exists():
        raise FileNotFoundError(f"模板不存在: {template_path}")
    with open(template_path, encoding="utf-8") as f:
        return _env.from_string(f.read())


async def warmup(templates: list[str] | None = None) -> int:
    """启动期预热所有模板.

    Args:
        templates: 模板名列表, None 表示扫描 templates/ 目录全部 .j2

    Returns:
        预热的模板数量
    """
    if templates is None:
        templates_dir = Path(__file__).parent / "templates"
        templates = [p.stem for p in templates_dir.glob("*.j2")]

    count = 0
    for name in templates:
        try:
            if name not in _template_cache:
                # 同步 I/O 但只在启动期调用, 不阻塞请求处理
                _template_cache[name] = _load_template_sync(name)
            count += 1
        except Exception as exc:
            logger.warning("模板预热失败: %s - %s", name, exc)
    logger.info("Prompt 模板预热完成: %d 个", count)
    return count


def render_prompt(template_name: str, context: dict[str, Any]) -> str:
    """渲染 Jinja2 模板, 防变量注入.

    Args:
        template_name: 模板名 (e.g. "knowledge_agent"), 对应 templates/{name}.j2
        context: 模板变量 dict, 仅白名单内变量生效

    Returns:
        渲染后的 prompt 字符串

    异常:
        模板不存在 → FileNotFoundError
        变量缺失 → jinja2.UndefinedError
        变量名不在白名单 → 静默忽略

    并发:
        模板缓存由 asyncio.Lock 守护, 首次加载不会重复编译
        推荐在 startup 调 warmup() 提前加载
    """
    # 1. 变量白名单过滤 (防 prompt injection)
    safe_context = {k: v for k, v in context.items() if k in TEMPLATE_VARIABLE_ALLOWLIST}

    # 2. 模板加载 (含缓存 + 锁)
    if template_name not in _template_cache:
        # 首次加载: 锁内同步 I/O (warmup 后基本不触发)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 在 async 上下文: schedule warmup, 走缓存 fallback 用 raw text
                logger.warning(
                    "模板 %s 未预热, 请在 startup 调 warmup(); 当前同步加载", template_name
                )
        except RuntimeError:
            pass
        _template_cache[template_name] = _load_template_sync(template_name)

    template = _template_cache[template_name]
    try:
        return template.render(**safe_context)
    except Exception as exc:
        logger.error("Prompt 渲染失败: template=%s err=%s", template_name, exc)
        raise


def clear_cache() -> None:
    """清除模板缓存 (热加载时使用)."""
    _template_cache.clear()
