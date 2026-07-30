package com.lumio.mcp.adapter.mock;

import com.lumio.mcp.domain.CardAccount;
import com.lumio.mcp.domain.InstallmentPlan;
import com.lumio.mcp.domain.LimitAdjustRecord;
import com.lumio.mcp.domain.RepaymentRecord;
import com.lumio.mcp.domain.TransactionRecord;
import jakarta.annotation.PostConstruct;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import org.springframework.stereotype.Component;

/**
 * 演示数据装载器：向 {@link InMemoryCardAccountRepository} 载入三张确定性演示卡。
 *
 * <p><b>安全红线：全部为构造的假数据，不代表任何真实持卡人，不连接任何真实系统。</b>
 * 由 Spring 在 Bean 初始化后自动调用 {@link #seed()}；单元测试亦可手动 {@code new MockDataSeeder(repo).seed()}
 * 以获得确定性数据。</p>
 */
@Component
public class MockDataSeeder {

    private final InMemoryCardAccountRepository repository;

    public MockDataSeeder(InMemoryCardAccountRepository repository) {
        this.repository = repository;
    }

    @PostConstruct
    public void seed() {
        CardAccount primary = new CardAccount(DemoCards.PRIMARY, "王**", "CNY");
        primary.setTotalLimit(new BigDecimal("50000.00"));
        primary.setUsedAmount(new BigDecimal("18650.00"));
        primary.setTempLimit(BigDecimal.ZERO);
        primary.setTempLimitExpiry(null);
        primary.setPoints(28560L);
        primary.setExpiringPoints(3200L);
        primary.setPointsExpiringDate(LocalDate.of(2026, 12, 31));
        primary.setStatementDate(LocalDate.of(2026, 7, 5));
        primary.setDueDate(LocalDate.of(2026, 7, 25));
        primary.setStatementAmount(new BigDecimal("8650.00"));
        primary.setMinPayment(new BigDecimal("865.00"));
        primary.setRepaidAmount(BigDecimal.ZERO);
        primary.setStatementIssued(true);
        primary.setCardStatus("正常");
        primary.setCardLevel("白金卡");
        primary.setBenefits(List.of("每年 6 次机场贵宾厅", "境外消费 1% 返现", "生日双倍积分", "全球紧急支援"));
        primary.setAnnualFee(new BigDecimal("2000.00"));
        primary.setAnnualFeeWaiverThreshold(12);
        primary.setCurrentYearSpendCount(8);
        primary.setAnnualFeeWaived(false);
        // 一笔在办分期与历史提额/还款记录（构造数据）
        primary.getInstallmentPlans().add(new InstallmentPlan(
                "IN20260615ABCD", new BigDecimal("6000.00"), 6, 4, new BigDecimal("1030.00"), "分期中"));
        primary.getRepaymentHistory().add(new RepaymentRecord(
                LocalDate.of(2026, 6, 24), new BigDecimal("5200.00"), "本人储蓄卡快捷", "成功"));
        primary.getRepaymentHistory().add(new RepaymentRecord(
                LocalDate.of(2026, 5, 23), new BigDecimal("4800.00"), "本人储蓄卡快捷", "成功"));
        primary.getLimitAdjustHistory().add(new LimitAdjustRecord(
                LocalDate.of(2026, 3, 1), "永久", new BigDecimal("40000.00"), new BigDecimal("50000.00"), "已生效"));
        repository.save(primary, List.of(
                txn(LocalDate.of(2026, 7, 20), "2026-07-20 12:35", "餐饮消费", "海底捞火锅", "328.00", "消费", "成功"),
                txn(LocalDate.of(2026, 7, 18), "2026-07-18 09:12", "线上购物", "京东商城", "1299.00", "消费", "成功"),
                txn(LocalDate.of(2026, 7, 15), "2026-07-15 20:41", "商超消费", "永辉超市", "486.50", "消费", "成功"),
                txn(LocalDate.of(2026, 7, 12), "2026-07-12 14:03", "退款", "京东商城", "-199.00", "退款", "成功"),
                txn(LocalDate.of(2026, 7, 6), "2026-07-06 08:00", "交通出行", "航空机票", "2360.00", "消费", "成功"),
                txn(LocalDate.of(2026, 6, 28), "2026-06-28 19:22", "餐饮消费", "星巴克", "58.00", "消费", "成功")));

        CardAccount secondary = new CardAccount(DemoCards.SECONDARY, "李**", "CNY");
        secondary.setTotalLimit(new BigDecimal("20000.00"));
        secondary.setUsedAmount(new BigDecimal("3200.00"));
        secondary.setTempLimit(new BigDecimal("5000.00"));
        secondary.setTempLimitExpiry(LocalDate.of(2026, 9, 30));
        secondary.setPoints(6120L);
        secondary.setExpiringPoints(0L);
        secondary.setPointsExpiringDate(null);
        secondary.setStatementDate(LocalDate.of(2026, 7, 8));
        secondary.setDueDate(LocalDate.of(2026, 7, 28));
        secondary.setStatementAmount(new BigDecimal("3200.00"));
        secondary.setMinPayment(new BigDecimal("320.00"));
        secondary.setRepaidAmount(new BigDecimal("3200.00"));
        secondary.setStatementIssued(true);
        secondary.setCardStatus("正常");
        secondary.setCardLevel("金卡");
        secondary.setBenefits(List.of("消费积分累计", "分期手续费 9 折"));
        secondary.setAnnualFee(new BigDecimal("300.00"));
        secondary.setAnnualFeeWaiverThreshold(6);
        secondary.setCurrentYearSpendCount(6);
        secondary.setAnnualFeeWaived(true);
        repository.save(secondary, List.of(
                txn(LocalDate.of(2026, 7, 19), "2026-07-19 10:05", "线上购物", "天猫超市", "268.00", "消费", "成功"),
                txn(LocalDate.of(2026, 7, 10), "2026-07-10 21:30", "还款", "本人储蓄卡", "-3200.00", "还款", "成功"),
                txn(LocalDate.of(2026, 7, 2), "2026-07-02 11:48", "餐饮消费", "肯德基", "72.00", "消费", "成功")));

        // 第三张卡：未激活新卡，用于开卡激活演示（构造数据）
        CardAccount fresh = new CardAccount(DemoCards.UNACTIVATED, "赵**", "CNY");
        fresh.setTotalLimit(new BigDecimal("10000.00"));
        fresh.setUsedAmount(BigDecimal.ZERO);
        fresh.setTempLimit(BigDecimal.ZERO);
        fresh.setTempLimitExpiry(null);
        fresh.setPoints(0L);
        fresh.setExpiringPoints(0L);
        fresh.setPointsExpiringDate(null);
        fresh.setStatementDate(LocalDate.of(2026, 7, 6));
        fresh.setDueDate(LocalDate.of(2026, 7, 26));
        fresh.setStatementAmount(BigDecimal.ZERO);
        fresh.setMinPayment(BigDecimal.ZERO);
        fresh.setRepaidAmount(BigDecimal.ZERO);
        fresh.setStatementIssued(false);
        fresh.setCardStatus("未激活");
        fresh.setCardLevel("金卡");
        fresh.setBenefits(List.of("新户首刷礼", "消费积分累计"));
        fresh.setAnnualFee(new BigDecimal("300.00"));
        fresh.setAnnualFeeWaiverThreshold(6);
        fresh.setCurrentYearSpendCount(0);
        fresh.setAnnualFeeWaived(false);
        repository.save(fresh, List.of());
    }

    private static TransactionRecord txn(LocalDate date, String time, String desc, String merchant,
                                         String amount, String type, String status) {
        return new TransactionRecord(date, time, desc, merchant, new BigDecimal(amount), type, status);
    }
}
