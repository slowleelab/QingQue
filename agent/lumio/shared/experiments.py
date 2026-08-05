"""B5: A/B 实验框架.

能力:
1. 粘性分桶 (sticky bucket): 同一 customer_id 总是进同一变体
2. 流量比例: 实验定义 v1: 50%, v2: 50%
3. 曝光 + 转化埋点
4. 统计显著性检验 (p-value < 0.05)
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from lumio.shared.config import ExperimentSettings, get_settings
from lumio.shared.logger import get_logger
from lumio.shared.metrics import AB_EXPERIMENT_CONVERSIONS, AB_EXPERIMENT_EXPOSURES

logger = get_logger(__name__)


class ExperimentStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    CONCLUDED = "concluded"


@dataclass
class Variant:
    """实验变体."""

    name: str  # "control" / "v1" / "v2"
    weight: float  # 0-100, 总和应为 100
    config: dict[str, Any] = field(default_factory=dict)  # 变体配置


@dataclass
class Experiment:
    """实验定义."""

    name: str
    description: str
    variants: list[Variant]
    status: ExperimentStatus = ExperimentStatus.DRAFT
    started_at: float | None = None
    ended_at: float | None = None
    min_sample_size: int = 1000
    p_value_threshold: float = 0.05

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "variants": [
                {"name": v.name, "weight": v.weight, "config": v.config} for v in self.variants
            ],
            "status": self.status.value,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


class ExperimentRegistry:
    """实验注册中心 (内存 + Redis 持久化)."""

    def __init__(self) -> None:
        self._experiments: dict[str, Experiment] = {}
        self._redis: Any = None

    def register(self, experiment: Experiment) -> None:
        """注册实验 (启动时调用)."""
        self._experiments[experiment.name] = experiment
        logger.info(
            "实验注册: name=%s variants=%s status=%s",
            experiment.name,
            [v.name for v in experiment.variants],
            experiment.status.value,
        )

    def assign_variant(self, experiment_name: str, customer_id: str) -> str | None:
        """为 customer 分配变体 (粘性).

        Returns:
            variant_name or None (实验未启用 / customer_id 缺失)
        """
        exp = self._experiments.get(experiment_name)
        if not exp or exp.status != ExperimentStatus.RUNNING:
            return None
        if not customer_id:
            return None

        # 粘性分桶: hash(customer_id) % 100
        h = int(hashlib.sha256(f"{experiment_name}:{customer_id}".encode()).hexdigest(), 16) % 100

        # 按 weight 累计
        cumulative = 0.0
        for variant in exp.variants:
            cumulative += variant.weight
            if h < cumulative:
                AB_EXPERIMENT_EXPOSURES.labels(
                    experiment=experiment_name, variant=variant.name
                ).inc()
                return variant.name

        # 默认返回最后一个 (兜底)
        return exp.variants[-1].name if exp.variants else None

    def track_conversion(
        self, experiment_name: str, customer_id: str, goal: str = "default"
    ) -> None:
        """记录转化 (埋点)."""
        variant = self.assign_variant(experiment_name, customer_id)
        if variant:
            AB_EXPERIMENT_CONVERSIONS.labels(
                experiment=experiment_name, variant=variant, goal=goal
            ).inc()

    def get_config(
        self, experiment_name: str, customer_id: str, default: Any = None
    ) -> Any:
        """获取变体配置 (替代硬编码, 业务代码调用)."""
        variant_name = self.assign_variant(experiment_name, customer_id)
        if not variant_name:
            return default
        exp = self._experiments.get(experiment_name)
        if not exp:
            return default
        for v in exp.variants:
            if v.name == variant_name:
                return v.config
        return default


# ── 统计显著性检验 (简化版) ──

def check_significance(
    control_conversions: int,
    control_total: int,
    variant_conversions: int,
    variant_total: int,
    p_threshold: float = 0.05,
) -> dict[str, Any]:
    """Z 检验比较两个变体的转化率.

    Returns:
        {
            "p_value": float,
            "significant": bool,
            "lift": float,  # 提升百分比
            "control_rate": float,
            "variant_rate": float,
        }
    """
    if control_total == 0 or variant_total == 0:
        return {"p_value": 1.0, "significant": False, "lift": 0.0}

    p_c = control_conversions / control_total
    p_v = variant_conversions / variant_total
    p_pool = (control_conversions + variant_conversions) / (control_total + variant_total)
    se = (p_pool * (1 - p_pool) * (1 / control_total + 1 / variant_total)) ** 0.5
    if se == 0:
        return {"p_value": 1.0, "significant": False, "lift": 0.0}

    z = (p_v - p_c) / se
    # 简化: z=1.96 对应 p=0.05
    p_value = 2 * (1 - _normal_cdf(abs(z)))
    significant = p_value < p_threshold
    lift = (p_v - p_c) / max(p_c, 1e-9) * 100

    return {
        "p_value": p_value,
        "significant": significant,
        "lift": lift,
        "control_rate": p_c,
        "variant_rate": p_v,
    }


def _normal_cdf(x: float) -> float:
    """标准正态分布 CDF (近似)."""
    import math

    return 0.5 * (1 + math.erf(x / (2**0.5)))


# ── 预置实验 ──

def register_default_experiments(registry: ExperimentRegistry) -> None:
    """注册默认实验 (在 app 启动时调用)."""
    # 实验 1: Prompt V2 vs V1
    registry.register(
        Experiment(
            name="prompt_v2_test",
            description="测试新版 prompt 是否提升 CSAT",
            variants=[
                Variant(name="control", weight=50.0, config={"prompt_version": "v1"}),
                Variant(name="treatment", weight=50.0, config={"prompt_version": "v2"}),
            ],
            status=ExperimentStatus.RUNNING,
        )
    )

    # 实验 2: KV cache 启用 vs 关闭
    registry.register(
        Experiment(
            name="kv_cache_enabled",
            description="KV cache 优化对 TTFT 的影响",
            variants=[
                Variant(name="control", weight=10.0, config={"kv_cache_enabled": False}),
                Variant(name="treatment", weight=90.0, config={"kv_cache_enabled": True}),
            ],
            status=ExperimentStatus.RUNNING,
        )
    )

    # 实验 3: A2UI 卡片 vs 纯文本
    registry.register(
        Experiment(
            name="a2ui_cards",
            description="A2UI 卡片对客户满意度影响",
            variants=[
                Variant(name="text_only", weight=50.0, config={"enable_cards": False}),
                Variant(name="with_cards", weight=50.0, config={"enable_cards": True}),
            ],
            status=ExperimentStatus.DRAFT,
        )
    )


# 全局单例
_registry: ExperimentRegistry | None = None


def get_experiment_registry() -> ExperimentRegistry:
    global _registry
    if _registry is None:
        _registry = ExperimentRegistry()
        register_default_experiments(_registry)
    return _registry
