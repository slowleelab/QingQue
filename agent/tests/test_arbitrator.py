"""全局仲裁器测试

覆盖: 优先级融合规则 + PII 脱敏 + 合规短语过滤。
"""
from __future__ import annotations

import pytest

from smartcs.services.assist.arbitrator import GlobalArbitrator
from smartcs.workflows.shared import ExecutorOutput, OrchestrationResult


# ── Fixtures ──


@pytest.fixture
def arbitrator() -> GlobalArbitrator:
    return GlobalArbitrator()


def _make_executor(
    executor_id: str = "",
    ui_schema: dict | None = None,
    risk_action: str = "",
) -> ExecutorOutput:
    return ExecutorOutput(
        executor_id=executor_id,
        ui_schema=ui_schema or {},
        risk_action=risk_action,
    )


# ── 优先级融合规则测试 ──


class TestGlobalArbitratorFusion:
    """优先级融合规则 (§3.5)"""

    @pytest.mark.asyncio
    async def test_risk_block_fusion(self, arbitrator: GlobalArbitrator):
        """风控 BLOCK: 主卡片=风控拦截, 营销=不展示"""
        risk = _make_executor(
            executor_id="risk",
            ui_schema={"alert": "涉嫌欺诈", "action": "BLOCK"},
            risk_action="BLOCK",
        )
        ai = _make_executor(
            executor_id="ai_service",
            ui_schema={"answer": "您的账单金额为500元"},
        )

        result = await arbitrator.arbitrate({"risk": risk, "ai_service": ai})

        assert isinstance(result, OrchestrationResult)
        assert result.fusion_type == "risk_blocked"
        assert result.primary_card["type"] == "risk_block"
        assert result.primary_card["content"] == risk.ui_schema
        assert result.marketing_slot is None

    @pytest.mark.asyncio
    async def test_risk_warn_fusion(self, arbitrator: GlobalArbitrator):
        """风控 WARN: 主卡片=服务, 风险标记=徽章, 营销=降级小卡片"""
        risk = _make_executor(
            executor_id="risk",
            ui_schema={"alerts": [{"level": "warn", "msg": "异常交易"}]},
            risk_action="WARN",
        )
        ai = _make_executor(
            executor_id="ai_service",
            ui_schema={"answer": "您的信用卡额度为5万元"},
        )
        mkt = _make_executor(
            executor_id="marketing",
            ui_schema={"marketing_cards": [{"title": "分期优惠"}]},
        )

        result = await arbitrator.arbitrate({"risk": risk, "ai_service": ai, "marketing": mkt})

        assert result.fusion_type == "service_risk_warn"
        assert result.primary_card["type"] == "service_answer"
        assert result.risk_badge is not None
        assert result.risk_badge["type"] == "risk_badge"
        assert result.risk_badge["alerts"] == [{"level": "warn", "msg": "异常交易"}]
        assert result.marketing_slot is not None
        assert result.marketing_slot["type"] == "marketing_small"

    @pytest.mark.asyncio
    async def test_risk_warn_no_marketing(self, arbitrator: GlobalArbitrator):
        """风控 WARN 但无营销卡片: 营销槽=None"""
        risk = _make_executor(
            executor_id="risk",
            ui_schema={"alerts": []},
            risk_action="WARN",
        )
        ai = _make_executor(
            executor_id="ai_service",
            ui_schema={"answer": "服务回答"},
        )
        mkt = _make_executor(
            executor_id="marketing",
            ui_schema={"marketing_cards": []},  # 空列表, 不算有卡片
        )

        result = await arbitrator.arbitrate({"risk": risk, "ai_service": ai, "marketing": mkt})

        assert result.fusion_type == "service_risk_warn"
        assert result.marketing_slot is None

    @pytest.mark.asyncio
    async def test_risk_pass_with_marketing(self, arbitrator: GlobalArbitrator):
        """风控 PASS + 有营销: 主卡片=服务, 辅助=营销(标准)"""
        ai = _make_executor(
            executor_id="ai_service",
            ui_schema={"answer": "服务回答"},
        )
        mkt = _make_executor(
            executor_id="marketing",
            ui_schema={"marketing_cards": [{"title": "推荐产品"}]},
        )

        result = await arbitrator.arbitrate({"ai_service": ai, "marketing": mkt})

        assert result.fusion_type == "service_marketing"
        assert result.primary_card["type"] == "service_answer"
        assert result.risk_badge is None
        assert result.marketing_slot is not None
        assert result.marketing_slot["type"] == "marketing_standard"

    @pytest.mark.asyncio
    async def test_service_only_fusion(self, arbitrator: GlobalArbitrator):
        """风控 PASS 但无营销: 仅服务"""
        ai = _make_executor(
            executor_id="ai_service",
            ui_schema={"answer": "服务回答"},
        )

        result = await arbitrator.arbitrate({"ai_service": ai})

        assert result.fusion_type == "service_only"
        assert result.primary_card["type"] == "service_answer"
        assert result.risk_badge is None
        assert result.marketing_slot is None

    @pytest.mark.asyncio
    async def test_empty_results(self, arbitrator: GlobalArbitrator):
        """所有执行器都未返回: 默认 PASS"""
        result = await arbitrator.arbitrate({})

        assert result.fusion_type == "service_only"
        assert result.primary_card["type"] == "service_answer"
        assert result.primary_card["content"] == {}


# ── PII 脱敏测试 ──


class TestPIIMasking:
    """PII 脱敏 (§4.2)"""

    def test_phone_masking(self, arbitrator: GlobalArbitrator):
        """手机号脱敏: 1[3-9]开头的11位数字"""
        assert arbitrator.mask_pii("请拨打13800138000联系") == "请拨打[PHONE]联系"
        assert arbitrator.mask_pii("手机号15912345678") == "手机号[PHONE]"
        assert arbitrator.mask_pii("18611112222是手机") == "[PHONE]是手机"

    def test_phone_not_masked_when_embedded(self, arbitrator: GlobalArbitrator):
        """嵌入在更长数字序列中的不应该是手机号"""
        # 12位数字不应被手机号匹配
        result = arbitrator.mask_pii("123456789012")
        # 会被银行卡规则匹配（16-19位不匹配12位），但仍不应被手机号匹配
        # 实际上12位不会被任何规则匹配
        assert "[PHONE]" not in result

    def test_idcard_masking(self, arbitrator: GlobalArbitrator):
        """身份证号脱敏: 18位"""
        assert arbitrator.mask_pii("身份证号110101199003076548") == "身份证号[IDCARD]"
        assert arbitrator.mask_pii("号码44030520001201234X") == "号码[IDCARD]"

    def test_bankcard_masking(self, arbitrator: GlobalArbitrator):
        """银行卡号脱敏: 16-19位数字"""
        assert arbitrator.mask_pii("卡号6222021234567890123") == "卡号[BANKCARD]"
        assert arbitrator.mask_pii("尾号6222021234567890") == "尾号[BANKCARD]"

    def test_name_masking_with_prefix(self, arbitrator: GlobalArbitrator):
        """姓名脱敏: '客户'后跟2-4个汉字"""
        assert arbitrator.mask_pii("客户张三") == "客户[NAME]"
        assert arbitrator.mask_pii("客户李四先生") == "客户[NAME]先生"
        assert arbitrator.mask_pii("姓名为王五") == "姓名为[NAME]"

    def test_name_masking_with_suffix(self, arbitrator: GlobalArbitrator):
        """姓名脱敏: 2-4个中文字后跟'先生/女士/同志'"""
        assert arbitrator.mask_pii("张三先生") == "[NAME]"
        assert arbitrator.mask_pii("李四女士") == "[NAME]"
        assert arbitrator.mask_pii("王小明同志") == "[NAME]"

    def test_recursive_dict_masking(self, arbitrator: GlobalArbitrator):
        """递归脱敏: 字典嵌套"""
        data = {
            "content": "客户张三的手机号13800138000",
            "nested": {
                "card": "银行卡6222021234567890",
            },
            "items": ["身份证110101199003076548"],
        }
        result = arbitrator._mask_pii_recursive(data)
        assert result["content"] == "客户[NAME]的手机号[PHONE]"
        assert result["nested"]["card"] == "银行卡[BANKCARD]"
        assert result["items"][0] == "身份证[IDCARD]"

    def test_no_pii_unchanged(self, arbitrator: GlobalArbitrator):
        """无 PII 内容不变"""
        text = "这是一条普通消息，不含任何敏感信息"
        assert arbitrator.mask_pii(text) == text

    def test_non_string_types_unchanged(self, arbitrator: GlobalArbitrator):
        """非字符串类型递归脱敏时不变"""
        assert arbitrator._mask_pii_recursive(42) == 42
        assert arbitrator._mask_pii_recursive(3.14) == 3.14
        assert arbitrator._mask_pii_recursive(True) is True
        assert arbitrator._mask_pii_recursive(None) is None


# ── 合规短语过滤测试 ──


class TestComplianceFilter:
    """合规短语过滤 (§3.5)"""

    def test_filter_blocked_phrase(self, arbitrator: GlobalArbitrator):
        """合规短语过滤: 违规短语替换"""
        assert arbitrator.filter_compliance("本产品保证收益") == "本产品[已过滤]"
        assert arbitrator.filter_compliance("投资稳赚不赔") == "投资[已过滤]"
        assert arbitrator.filter_compliance("零风险高回报") == "[已过滤]高回报"

    def test_filter_multiple_phrases(self, arbitrator: GlobalArbitrator):
        """合规短语过滤: 多个违规短语"""
        text = "保证收益且稳赚不赔"
        result = arbitrator.filter_compliance(text)
        assert "[已过滤]" in result
        assert "保证收益" not in result
        assert "稳赚不赔" not in result

    def test_no_blocked_phrase_unchanged(self, arbitrator: GlobalArbitrator):
        """合规短语过滤: 无违规短语不变"""
        text = "正常的服务话术内容"
        assert arbitrator.filter_compliance(text) == text

    def test_compliance_filter_in_arbitrate(self, arbitrator: GlobalArbitrator):
        """仲裁时自动过滤合规短语"""
        # 同步方式验证递归合规过滤
        result = arbitrator._filter_compliance_recursive(
            {"answer": "本产品保证收益，绝对安全"}
        )
        assert result["answer"] == "本产品[已过滤]，[已过滤]"


# ── 集成: 仲裁 + PII 脱敏联动测试 ──


class TestArbitrateWithPIIMasking:
    """仲裁 + PII 脱敏联动"""

    @pytest.mark.asyncio
    async def test_arbitrate_masks_pii_in_primary_card(self, arbitrator: GlobalArbitrator):
        """仲裁结果中主卡片 PII 被脱敏"""
        ai = _make_executor(
            executor_id="ai_service",
            ui_schema={"answer": "客户张三的手机号13800138000"},
        )
        result = await arbitrator.arbitrate({"ai_service": ai})
        content = result.primary_card["content"]
        assert "13800138000" not in str(content)
        assert "[PHONE]" in str(content)
        assert "[NAME]" in str(content)

    @pytest.mark.asyncio
    async def test_arbitrate_masks_pii_in_risk_badge(self, arbitrator: GlobalArbitrator):
        """仲裁结果中风险徽章 PII 被脱敏"""
        risk = _make_executor(
            executor_id="risk",
            ui_schema={"alerts": [{"msg": "客户张三手机13800138000异常"}]},
            risk_action="WARN",
        )
        ai = _make_executor(
            executor_id="ai_service",
            ui_schema={"answer": "服务回答"},
        )
        result = await arbitrator.arbitrate({"risk": risk, "ai_service": ai})
        badge_str = str(result.risk_badge)
        assert "13800138000" not in badge_str
        assert "[PHONE]" in badge_str

    @pytest.mark.asyncio
    async def test_arbitrate_masks_pii_in_marketing_slot(self, arbitrator: GlobalArbitrator):
        """仲裁结果中营销槽 PII 被脱敏"""
        ai = _make_executor(
            executor_id="ai_service",
            ui_schema={"answer": "服务回答"},
        )
        mkt = _make_executor(
            executor_id="marketing",
            ui_schema={
                "marketing_cards": [{"title": "客户李四专属卡6222021234567890"}],
            },
        )
        result = await arbitrator.arbitrate({"ai_service": ai, "marketing": mkt})
        slot_str = str(result.marketing_slot)
        assert "6222021234567890" not in slot_str
        assert "[BANKCARD]" in slot_str
