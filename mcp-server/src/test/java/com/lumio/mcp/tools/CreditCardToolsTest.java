package com.lumio.mcp.tools;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.lumio.mcp.adapter.mock.DemoCards;
import com.lumio.mcp.adapter.mock.InMemoryCardAccountRepository;
import com.lumio.mcp.adapter.mock.InMemoryIdempotencyStore;
import com.lumio.mcp.adapter.mock.MockDataSeeder;
import com.lumio.mcp.config.CreditCardProperties;
import com.lumio.mcp.domain.exception.BusinessException;
import com.lumio.mcp.domain.service.BillingService;
import com.lumio.mcp.domain.service.CardLifecycleService;
import com.lumio.mcp.domain.service.CreditLimitService;
import com.lumio.mcp.domain.service.IdempotentExecutor;
import com.lumio.mcp.domain.service.InstallmentService;
import com.lumio.mcp.domain.service.PaymentService;
import com.lumio.mcp.domain.service.PointsService;
import com.lumio.mcp.domain.service.TransactionService;
import com.lumio.mcp.model.AnnualFeeInfo;
import com.lumio.mcp.model.AutoRepayResult;
import com.lumio.mcp.model.BillDetail;
import com.lumio.mcp.model.CardActivationResult;
import com.lumio.mcp.model.CardBenefitsInfo;
import com.lumio.mcp.model.CardBill;
import com.lumio.mcp.model.CardLostResult;
import com.lumio.mcp.model.CardStatusInfo;
import com.lumio.mcp.model.CreditLimitInfo;
import com.lumio.mcp.model.DisputeResult;
import com.lumio.mcp.model.InstallmentCancelResult;
import com.lumio.mcp.model.InstallmentOffer;
import com.lumio.mcp.model.InstallmentResult;
import com.lumio.mcp.model.InstallmentStatusInfo;
import com.lumio.mcp.model.LimitAdjustHistoryInfo;
import com.lumio.mcp.model.PermanentLimitResult;
import com.lumio.mcp.model.PointsInfo;
import com.lumio.mcp.model.PointsRedeemResult;
import com.lumio.mcp.model.RepaymentHistoryInfo;
import com.lumio.mcp.model.RepaymentResult;
import com.lumio.mcp.model.TempLimitResult;
import com.lumio.mcp.model.TransactionPage;
import java.math.BigDecimal;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * 22 个信用卡 MCP 工具的行为与入参校验单元测试（纯内存 Mock + 领域服务，无 Spring 上下文）。
 */
class CreditCardToolsTest {

    private static final String CARD = DemoCards.PRIMARY;
    private static final String FRESH_CARD = DemoCards.UNACTIVATED;
    /** 格式合法但不存在的卡号（用于「未找到账户」场景）。 */
    private static final String UNKNOWN_CARD = "6225880099999999";

    private BillTools billTools;
    private CreditLimitTools creditLimitTools;
    private PointsTools pointsTools;
    private TransactionTools transactionTools;
    private InstallmentTools installmentTools;
    private PaymentTools paymentTools;
    private CardServiceTools cardServiceTools;

    @BeforeEach
    void setUp() {
        InMemoryCardAccountRepository repository = new InMemoryCardAccountRepository();
        new MockDataSeeder(repository).seed();
        CreditCardProperties props = new CreditCardProperties();
        IdempotentExecutor idempotent = new IdempotentExecutor(new InMemoryIdempotencyStore(props));

        billTools = new BillTools(new BillingService(repository), props);
        pointsTools = new PointsTools(new PointsService(repository, idempotent), props);
        transactionTools = new TransactionTools(new TransactionService(repository), props);
        creditLimitTools = new CreditLimitTools(new CreditLimitService(repository, idempotent, props), props);
        installmentTools = new InstallmentTools(new InstallmentService(repository, idempotent, props), props);
        paymentTools = new PaymentTools(new PaymentService(repository, idempotent, props), props);
        cardServiceTools = new CardServiceTools(new CardLifecycleService(repository, idempotent), props);
    }

    // ── 只读工具 ──

    @Test
    void queryCardBillReturnsOutstanding() {
        CardBill bill = billTools.queryCardBill(CARD);
        assertThat(bill.cardNo()).isEqualTo(CARD);
        assertThat(bill.statementAmount()).isEqualByComparingTo("8650.00");
        assertThat(bill.outstandingAmount()).isEqualByComparingTo("8650.00");
        assertThat(bill.status()).contains("未还清");
    }

    @Test
    void queryBillDetailFiltersByCycle() {
        BillDetail detail = billTools.queryBillDetail(CARD, "2026-07");
        assertThat(detail.billingCycle()).isEqualTo("2026-07");
        assertThat(detail.count()).isEqualTo(detail.items().size());
        assertThat(detail.count()).isGreaterThan(0);
        assertThat(detail.totalRefund()).isGreaterThanOrEqualTo(BigDecimal.ZERO);
    }

    @Test
    void queryCreditLimitComputesAvailable() {
        CreditLimitInfo info = creditLimitTools.queryCreditLimit(CARD);
        // 可用 = 固定 50000 + 临时 0 - 已用 18650
        assertThat(info.availableAmount()).isEqualByComparingTo("31350.00");
    }

    @Test
    void queryPointsReturnsBalance() {
        PointsInfo points = pointsTools.queryPoints(CARD);
        assertThat(points.balance()).isEqualTo(28560L);
        assertThat(points.expiringPoints()).isEqualTo(3200L);
    }

    @Test
    void queryTransactionsFiltersByDate() {
        TransactionPage page = transactionTools.queryTransactions(CARD, "2026-07-01", "2026-07-31");
        assertThat(page.count()).isEqualTo(page.transactions().size());
        assertThat(page.transactions()).allSatisfy(t -> assertThat(t.txnTime()).startsWith("2026-07"));
    }

    @Test
    void queryInstallmentOfferReturnsFourPlans() {
        InstallmentOffer offer = installmentTools.queryInstallmentOffer(CARD);
        assertThat(offer.plans()).hasSize(4);
        assertThat(offer.plans()).extracting(InstallmentOffer.Plan::periods)
                .containsExactly(3, 6, 12, 24);
    }

    // ── 敏感/写工具 ──

    @Test
    void applyBillInstallmentSucceedsWithinOutstanding() {
        InstallmentResult result = installmentTools.applyBillInstallment(CARD, 6000, 6, null);
        assertThat(result.status()).isEqualTo("已受理");
        assertThat(result.periods()).isEqualTo(6);
        assertThat(result.referenceNo()).startsWith("IN");
        assertThat(result.perPeriodTotal()).isGreaterThan(BigDecimal.ZERO);
    }

    @Test
    void repayCreditCardReducesOutstanding() {
        RepaymentResult result = paymentTools.repayCreditCard(CARD, 1000, null, null);
        assertThat(result.status()).isEqualTo("已受理");
        assertThat(result.channel()).isNotBlank();
        assertThat(result.outstandingAfter()).isEqualByComparingTo("7650.00");
        // 再次查询账单应体现已还金额
        assertThat(billTools.queryCardBill(CARD).repaidAmount()).isEqualByComparingTo("1000");
    }

    @Test
    void adjustTempCreditLimitTakesEffect() {
        TempLimitResult result = creditLimitTools.adjustTempCreditLimit(CARD, 20000, "2026-10-31", null);
        assertThat(result.status()).isEqualTo("已生效");
        assertThat(result.newTempLimit()).isEqualByComparingTo("20000");
        // 生效后可用额度增加
        assertThat(creditLimitTools.queryCreditLimit(CARD).tempLimit()).isEqualByComparingTo("20000");
    }

    @Test
    void reportCardLostChangesStatus() {
        CardLostResult result = cardServiceTools.reportCardLost(CARD, "挂失", true, null);
        assertThat(result.action()).isEqualTo("挂失");
        assertThat(result.reissue()).isTrue();
        assertThat(result.referenceNo()).startsWith("LS");
    }

    // ── 新增只读工具 ──

    @Test
    void queryAnnualFeeReturnsWaiverProgress() {
        // 主卡阈值 12 笔，当前 8 笔 → 未减免，还差 4 笔
        AnnualFeeInfo info = billTools.queryAnnualFee(CARD);
        assertThat(info.annualFee()).isEqualByComparingTo("2000.00");
        assertThat(info.waived()).isFalse();
        assertThat(info.waiverThreshold()).isEqualTo(12);
        assertThat(info.waiverRule()).contains("还差 4 笔");
    }

    @Test
    void queryAnnualFeeReflectsWaivedCard() {
        // 副卡阈值 6 笔，已刷 6 笔 → 已减免
        AnnualFeeInfo info = billTools.queryAnnualFee(DemoCards.SECONDARY);
        assertThat(info.waived()).isTrue();
    }

    @Test
    void queryLimitAdjustHistoryReturnsSeededRecords() {
        LimitAdjustHistoryInfo info = creditLimitTools.queryLimitAdjustHistory(CARD);
        assertThat(info.count()).isEqualTo(info.items().size());
        assertThat(info.count()).isGreaterThanOrEqualTo(1);
        assertThat(info.items()).anySatisfy(i -> assertThat(i.type()).isEqualTo("永久"));
    }

    @Test
    void queryCardBenefitsReturnsLevelAndBenefits() {
        CardBenefitsInfo info = pointsTools.queryCardBenefits(CARD);
        assertThat(info.cardLevel()).isEqualTo("白金卡");
        assertThat(info.benefits()).isNotEmpty();
    }

    @Test
    void queryInstallmentStatusReturnsSeededPlan() {
        InstallmentStatusInfo info = installmentTools.queryInstallmentStatus(CARD);
        assertThat(info.count()).isEqualTo(info.plans().size());
        assertThat(info.plans()).anySatisfy(p -> assertThat(p.status()).isEqualTo("分期中"));
    }

    @Test
    void queryRepaymentHistoryReturnsRecordsDescending() {
        RepaymentHistoryInfo info = paymentTools.queryRepaymentHistory(CARD);
        assertThat(info.count()).isEqualTo(info.items().size());
        assertThat(info.count()).isGreaterThanOrEqualTo(2);
        // 倒序：首条日期不早于次条
        assertThat(info.items().get(0).date()).isAfterOrEqualTo(info.items().get(1).date());
    }

    @Test
    void queryCardStatusReturnsNormalForActiveCard() {
        CardStatusInfo info = cardServiceTools.queryCardStatus(CARD);
        assertThat(info.status()).isEqualTo("正常");
        assertThat(info.active()).isTrue();
    }

    @Test
    void queryCardStatusReturnsUnactivatedForFreshCard() {
        CardStatusInfo info = cardServiceTools.queryCardStatus(FRESH_CARD);
        assertThat(info.status()).isEqualTo("未激活");
        assertThat(info.active()).isFalse();
    }

    // ── 新增敏感/写工具 ──

    @Test
    void cancelInstallmentMarksPlanCancelled() {
        String planId = installmentTools.queryInstallmentStatus(CARD).plans().get(0).planId();
        InstallmentCancelResult result = installmentTools.cancelInstallment(CARD, planId, null);
        assertThat(result.status()).isEqualTo("已取消");
        assertThat(result.referenceNo()).startsWith("CI");
        // 再查该计划状态为已取消
        assertThat(installmentTools.queryInstallmentStatus(CARD).plans())
                .anySatisfy(p -> {
                    if (p.planId().equals(planId)) {
                        assertThat(p.status()).isEqualTo("已取消");
                    }
                });
    }

    @Test
    void cancelUnknownInstallmentRejected() {
        assertThatThrownBy(() -> installmentTools.cancelInstallment(CARD, "IN-NOT-EXIST", null))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("分期");
    }

    @Test
    void setAutoRepayEnablesFullMode() {
        AutoRepayResult result = paymentTools.setAutoRepay(CARD, true, "全额", null, null);
        assertThat(result.enabled()).isTrue();
        assertThat(result.mode()).isEqualTo("全额");
        assertThat(result.referenceNo()).startsWith("AR");
    }

    @Test
    void setAutoRepayInvalidModeRejected() {
        assertThatThrownBy(() -> paymentTools.setAutoRepay(CARD, true, "随便", null, null))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("全额");
    }

    @Test
    void applyPermanentLimitEntersReview() {
        PermanentLimitResult result = creditLimitTools.applyPermanentLimit(CARD, 80000, null);
        assertThat(result.status()).isEqualTo("审核中");
        assertThat(result.referenceNo()).startsWith("PL");
        // 永久提额不即时改变固定额度
        assertThat(creditLimitTools.queryCreditLimit(CARD).totalLimit()).isEqualByComparingTo("50000.00");
    }

    @Test
    void applyPermanentLimitBelowCurrentRejected() {
        assertThatThrownBy(() -> creditLimitTools.applyPermanentLimit(CARD, 30000, null))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("高于");
    }

    @Test
    void redeemPointsDeductsBalance() {
        long before = pointsTools.queryPoints(CARD).balance();
        PointsRedeemResult result = pointsTools.redeemPoints(CARD, "100元京东E卡", 10000, null);
        assertThat(result.status()).isEqualTo("已受理");
        assertThat(result.referenceNo()).startsWith("RD");
        assertThat(result.pointsBalanceAfter()).isEqualTo(before - 10000);
    }

    @Test
    void redeemPointsInsufficientRejected() {
        assertThatThrownBy(() -> pointsTools.redeemPoints(CARD, "豪华大礼包", 99999999L, null))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("积分");
    }

    @Test
    void activateCardActivatesFreshCard() {
        CardActivationResult result = cardServiceTools.activateCard(FRESH_CARD, null);
        assertThat(result.status()).isEqualTo("正常");
        assertThat(result.referenceNo()).startsWith("AC");
        assertThat(cardServiceTools.queryCardStatus(FRESH_CARD).active()).isTrue();
    }

    @Test
    void activateAlreadyActiveCardRejected() {
        assertThatThrownBy(() -> cardServiceTools.activateCard(CARD, null))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("激活");
    }

    @Test
    void reportTransactionDisputeAccepted() {
        DisputeResult result = cardServiceTools.reportTransactionDispute(CARD, "TXN-0001", "未授权交易", null);
        assertThat(result.status()).isEqualTo("已受理");
        assertThat(result.referenceNo()).startsWith("DP");
    }

    @Test
    void duplicateDisputeRejected() {
        cardServiceTools.reportTransactionDispute(CARD, "TXN-0002", "重复扣款", null);
        assertThatThrownBy(() -> cardServiceTools.reportTransactionDispute(CARD, "TXN-0002", "重复扣款", null))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("争议");
    }

    // ── 入参校验 ──

    @Test
    void invalidCardNoRejected() {
        assertThatThrownBy(() -> billTools.queryCardBill("12"))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("卡号");
    }

    @Test
    void unknownCardRejected() {
        assertThatThrownBy(() -> billTools.queryCardBill(UNKNOWN_CARD))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("未找到");
    }

    @Test
    void repayNonPositiveAmountRejected() {
        assertThatThrownBy(() -> paymentTools.repayCreditCard(CARD, 0, null, null))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("大于 0");
    }

    @Test
    void installmentAmountOverOutstandingRejected() {
        assertThatThrownBy(() -> installmentTools.applyBillInstallment(CARD, 999999, 6, null))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("超过");
    }

    @Test
    void installmentUnsupportedPeriodsRejected() {
        assertThatThrownBy(() -> installmentTools.applyBillInstallment(CARD, 1000, 9, null))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("期数");
    }

    @Test
    void tempLimitOverCapRejected() {
        // 固定额度 50000，上限为 2 倍 = 100000
        assertThatThrownBy(() -> creditLimitTools.adjustTempCreditLimit(CARD, 150000, null, null))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("上限");
    }
}
