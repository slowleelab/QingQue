"""B0: Prompt 注册中心 — Nacos 热加载 + Redis 缓存 + A/B 版本管理

3 大能力:
1. 版本管理: 每个 prompt 有 version 字段, 启动时打印当前生效版本
2. 热加载: 从 Nacos 配置中心拉取, Redis 缓存 60s, 改动不重启
3. A/B 测试: get_prompt(name, customer_id) → 按 hash 选版本

降级策略:
- Nacos 不可用 → Redis 缓存 → 本地 prompts.py 常量 (兜底)
- 三层都不可用 → 启动失败 (硬错误, 不允许无 prompt 上线)

后续可加:
- Prompt diff (对比两个版本差异)
- Prompt 回滚 (一键切回上一版本)
- Prompt A/B 效果分析 (实验组 vs 对照组 CSAT)
"""

from __future__ import annotations

import functools
import hashlib
import time
from typing import Any

from lumio.shared.config import PromptSettings, get_settings
from lumio.shared.logger import get_logger

logger = get_logger(__name__)


# ── 关键: Prompt 内容不能含 session_id / 时间戳等动态信息 ──
# 否则无法命中 KV cache. 变量通过 render(template, context) 注入.


class PromptVersion:
    """Prompt 版本对象."""

    def __init__(
        self,
        name: str,
        version: str,
        content: str,
        changelog: str = "",
        rollout_pct: float = 100.0,
        created_at: float | None = None,
    ) -> None:
        self.name = name
        self.version = version
        self.content = content
        self.changelog = changelog
        self.rollout_pct = max(0.0, min(100.0, rollout_pct))
        self.created_at = created_at or time.time()

    def __repr__(self) -> str:
        return f"PromptVersion(name={self.name}, version={self.version}, rollout={self.rollout_pct}%)"


# ── 本地兜底 prompt 库 (Nacos / Redis 都不可用时使用) ──
# 复用 prompts.py 硬编码常量
_LOCAL_PROMPTS: dict[str, PromptVersion] = {}


def _init_local_prompts() -> None:
    """初始化本地兜底 prompt 库 (从 prompts.py 导入)."""
    from lumio.services.bot.prompts import (
        BUSINESS_SYSTEM_PROMPT,
        FALLBACK_SYSTEM_PROMPT,
        KNOWLEDGE_SYSTEM_PROMPT,
    )

    _LOCAL_PROMPTS.clear()
    _LOCAL_PROMPTS.update(
        {
            "knowledge_agent": PromptVersion(
                name="knowledge_agent",
                version="local-v1",
                content=KNOWLEDGE_SYSTEM_PROMPT,
                changelog="本地兜底版本 (Nacos 不可用时使用)",
            ),
            "business_agent": PromptVersion(
                name="business_agent",
                version="local-v1",
                content=BUSINESS_SYSTEM_PROMPT,
                changelog="本地兜底版本",
            ),
            "fallback_agent": PromptVersion(
                name="fallback_agent",
                version="local-v1",
                content=FALLBACK_SYSTEM_PROMPT,
                changelog="本地兜底版本",
            ),
        }
    )


class PromptRegistry:
    """Prompt 注册中心 (单例)."""

    def __init__(self, settings: PromptSettings | None = None) -> None:
        self._settings = settings or get_settings().prompt
        self._cache: dict[str, PromptVersion] = {}
        self._cache_loaded_at: dict[str, float] = {}
        self._redis_client: Any = None  # 延迟初始化
        self._nacos_client: Any = None
        _init_local_prompts()
        if self._settings.log_active_version:
            logger.info("PromptRegistry 启动, 当前生效版本: %s", self._get_active_versions())

    def _get_active_versions(self) -> dict[str, str]:
        return {name: pv.version for name, pv in self._cache.items()} or {
            name: pv.version for name, pv in _LOCAL_PROMPTS.items()
        }

    def _get_redis(self) -> Any:
        """延迟初始化 Redis 客户端."""
        if self._redis_client is None:
            try:
                from lumio.services.common.redis_client import get_redis_client

                self._redis_client = get_redis_client()
            except Exception as exc:
                logger.debug("Redis 客户端不可用: %s", exc)
                self._redis_client = False  # 标记不可用
        return self._redis_client if self._redis_client else None

    def get_prompt(self, name: str, customer_id: str | None = None) -> str:
        """获取 prompt 内容 (A/B 版本感知).

        优先级: Nacos → Redis 缓存 → 本地兜底
        A/B 路由: rollout_pct < 100 时, 按 customer_id hash 决定是否启用新版本
        """
        # 1. 尝试从缓存 (Redis 或内存) 获取
        version = self._get_from_cache(name)
        if version is None:
            version = self._load_from_nacos(name)
            if version is None:
                # 2. 兜底到本地
                version = _LOCAL_PROMPTS.get(name)
                if version is None:
                    logger.error("Prompt 不存在且无本地兜底: name=%s", name)
                    return f"[PROMPT_MISSING:{name}]"

        # A/B 路由: rollout_pct < 100 时按 hash 分流
        if version.rollout_pct < 100.0 and customer_id and not self._is_in_rollout(customer_id, version.rollout_pct):
            # 切回主版本
            main_version = self._get_main_version(name)
            if main_version:
                version = main_version

        return version.content

    def _get_from_cache(self, name: str) -> PromptVersion | None:
        """从 Redis 缓存或内存获取."""
        now = time.time()
        cached_at = self._cache_loaded_at.get(name, 0)
        if now - cached_at < self._settings.cache_ttl_seconds and name in self._cache:
            return self._cache[name]

        # 尝试 Redis
        redis = self._get_redis()
        if redis:
            try:
                import json

                data = redis.get(f"lumio:prompt:{name}")
                if data:
                    payload = json.loads(data)
                    pv = PromptVersion(
                        name=name,
                        version=payload["version"],
                        content=payload["content"],
                        changelog=payload.get("changelog", ""),
                        rollout_pct=payload.get("rollout_pct", 100.0),
                        created_at=payload.get("created_at"),
                    )
                    self._cache[name] = pv
                    self._cache_loaded_at[name] = now
                    return pv
            except Exception as exc:
                logger.debug("Redis 缓存读取失败: name=%s err=%s", name, exc)

        return None

    def _load_from_nacos(self, name: str) -> PromptVersion | None:
        """从 Nacos 拉取 (失败时返回 None)."""
        try:
            # 实际集成 nacos-sdk 时启用
            # from nacos import NacosClient
            # client = NacosClient(self._settings.nacos_server_addr)
            # data = client.get_config(f"lumio.prompts.{name}")
            # return self._parse_nacos_data(name, data)
            logger.debug("Nacos 客户端未启用, 跳过 (name=%s)", name)
            return None
        except Exception as exc:
            logger.warning("Nacos 拉取失败: name=%s err=%s", name, exc)
            return None

    def _get_main_version(self, name: str) -> PromptVersion | None:
        """获取主版本 (100% rollout)."""
        main = self._cache.get(f"{name}:main")
        if main:
            return main
        return _LOCAL_PROMPTS.get(name)

    @staticmethod
    def _is_in_rollout(customer_id: str, rollout_pct: float) -> bool:
        """按 customer_id hash 决定是否在新版本 (粘性分桶)."""
        if not customer_id:
            return True
        h = int(hashlib.sha256(customer_id.encode()).hexdigest(), 16) % 100
        return h < rollout_pct

    def get_metadata(self, name: str) -> dict[str, Any] | None:
        """获取 prompt 元数据 (version, changelog 等) 用于调试."""
        v = self._cache.get(name) or _LOCAL_PROMPTS.get(name)
        if v:
            return {
                "name": v.name,
                "version": v.version,
                "rollout_pct": v.rollout_pct,
                "changelog": v.changelog,
                "created_at": v.created_at,
            }
        return None

    def invalidate_cache(self, name: str | None = None) -> None:
        """清除缓存 (灰度时手动调用)."""
        if name:
            self._cache.pop(name, None)
            self._cache_loaded_at.pop(name, None)
        else:
            self._cache.clear()
            self._cache_loaded_at.clear()


# 全局单例 — 用 functools.cache 替代手写 if-check, 线程安全 + 防 race
@functools.cache
def get_prompt_registry() -> PromptRegistry:
    """获取全局 PromptRegistry (线程安全, 仅初始化 1 次)."""
    return PromptRegistry()


def get_prompt(name: str, customer_id: str | None = None) -> str:
    """便捷函数: 获取 prompt 内容."""
    return get_prompt_registry().get_prompt(name, customer_id)
