"""A/B 实验框架单元测试 (experiments.py)"""

from __future__ import annotations

from lumio.shared.experiments import (
    Experiment,
    ExperimentRegistry,
    ExperimentStatus,
    Variant,
    check_significance,
    get_experiment_registry,
    register_default_experiments,
)

# ── 数据模型 ──


def test_experiment_status_enum():
    """4 个实验状态"""
    assert ExperimentStatus.RUNNING.value == "running"
    assert ExperimentStatus.DRAFT.value == "draft"
    assert ExperimentStatus.PAUSED.value == "paused"
    assert ExperimentStatus.CONCLUDED.value == "concluded"


def test_variant_defaults():
    """Variant 缺省 config 为空 dict"""
    v = Variant(name="control", weight=50.0)
    assert v.config == {}


def test_experiment_to_dict():
    """Experiment 序列化"""
    exp = Experiment(
        name="e1",
        description="测试",
        variants=[Variant(name="control", weight=50.0, config={"v": 1})],
        status=ExperimentStatus.RUNNING,
        started_at=1.0,
    )
    data = exp.to_dict()
    assert data["name"] == "e1"
    assert data["status"] == "running"
    assert data["variants"][0]["name"] == "control"
    assert data["started_at"] == 1.0
    assert data["ended_at"] is None


# ── 注册与粘性分桶 ──


def _make_running_exp(name: str = "exp1") -> Experiment:
    return Experiment(
        name=name,
        description="d",
        variants=[
            Variant(name="control", weight=50.0, config={"version": "v1"}),
            Variant(name="treatment", weight=50.0, config={"version": "v2"}),
        ],
        status=ExperimentStatus.RUNNING,
    )


def test_register_and_assign_running():
    """RUNNING 实验能分配变体, 且粘性 (同一 customer 结果稳定)"""
    reg = ExperimentRegistry()
    reg.register(_make_running_exp())
    v1 = reg.assign_variant("exp1", "cust-1")
    v2 = reg.assign_variant("exp1", "cust-1")
    assert v1 in {"control", "treatment"}
    assert v1 == v2  # 粘性


def test_assign_variant_draft_returns_none():
    """DRAFT 实验不分配"""
    reg = ExperimentRegistry()
    reg.register(Experiment(name="draft", description="d", variants=[Variant(name="v", weight=100)]))
    assert reg.assign_variant("draft", "cust-1") is None


def test_assign_variant_unknown_exp():
    """未注册实验返回 None"""
    reg = ExperimentRegistry()
    assert reg.assign_variant("no_such", "cust-1") is None


def test_assign_variant_no_customer():
    """customer_id 缺失返回 None"""
    reg = ExperimentRegistry()
    reg.register(_make_running_exp())
    assert reg.assign_variant("exp1", "") is None


def test_assign_variant_weight_distribution():
    """单变体 100% 权重必然命中"""
    reg = ExperimentRegistry()
    reg.register(
        Experiment(
            name="w",
            description="d",
            variants=[Variant(name="only", weight=100.0)],
            status=ExperimentStatus.RUNNING,
        )
    )
    assert reg.assign_variant("w", "any") == "only"


def test_track_conversion():
    """转化埋点: RUNNING 实验记录, 其他不记录"""
    reg = ExperimentRegistry()
    reg.register(_make_running_exp())
    reg.track_conversion("exp1", "cust-9", goal="apply")
    # 无异常即通过; DRAFT 实验不产生转换
    reg.register(Experiment(name="draft", description="d", variants=[Variant(name="v", weight=100)]))
    reg.track_conversion("draft", "cust-9")


def test_get_config():
    """get_config 返回命中变体的配置"""
    reg = ExperimentRegistry()
    reg.register(_make_running_exp())
    cfg = reg.get_config("exp1", "cust-2")
    assert cfg in ({"version": "v1"}, {"version": "v2"})
    # 未注册实验返回 default
    assert reg.get_config("no_such", "cust-2", default="d") == "d"


# ── 显著性检验 ──


def test_check_significance_zero_total():
    """任一侧样本数为 0 → 不显著"""
    result = check_significance(0, 0, 10, 100)
    assert not result["significant"]
    assert result["p_value"] == 1.0
    assert result["lift"] == 0.0


def test_check_significance_same_rates():
    """转化率相同 → 不显著"""
    result = check_significance(10, 100, 10, 100)
    assert not result["significant"]
    assert abs(result["lift"]) < 1e-6


def test_check_significance_different():
    """转化率差距大 → 显著 + 正 lift"""
    result = check_significance(20, 100, 50, 100)
    assert result["significant"]
    assert result["lift"] > 0
    assert result["control_rate"] == 0.2
    assert result["variant_rate"] == 0.5


# ── 预置实验与单例 ──


def test_register_default_experiments():
    """预置 3 个实验: 2 RUNNING + 1 DRAFT"""
    reg = ExperimentRegistry()
    register_default_experiments(reg)
    assert "prompt_v2_test" in reg._experiments
    assert "kv_cache_enabled" in reg._experiments
    assert reg._experiments["a2ui_cards"].status == ExperimentStatus.DRAFT


def test_get_experiment_registry_singleton():
    """单例: 多次获取同一实例且含预置实验"""
    reg1 = get_experiment_registry()
    reg2 = get_experiment_registry()
    assert reg1 is reg2
    assert reg1.assign_variant("prompt_v2_test", "cust-x") in {"control", "treatment"}
