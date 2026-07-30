package com.smartcs.mcp.domain;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;

/**
 * 信用卡账户聚合实体（<b>仅用于演示，不代表任何真实持卡人数据</b>）。
 *
 * <p>以完整卡号 {@code cardNo} 作为账户标识（构造的假卡号，非真实 PAN），
 * 绝不存储 CVV、有效期等敏感要素。写类工具通过
 * {@link com.smartcs.mcp.domain.port.CardAccountRepository#updateAtomically} 在持锁临界区内
 * 修改本对象状态以模拟受理效果，但不落任何真实系统。</p>
 */
public class CardAccount {

    /** 完整卡号（账户标识，mock 假卡号）。 */
    private final String cardNo;
    /** 持卡人称呼（脱敏，如「王**」）。 */
    private final String holderMasked;
    /** 币种。 */
    private final String currency;

    /** 固定信用额度。 */
    private BigDecimal totalLimit;
    /** 已用额度。 */
    private BigDecimal usedAmount;
    /** 临时额度（无则为 0）。 */
    private BigDecimal tempLimit;
    /** 临时额度到期日。 */
    private LocalDate tempLimitExpiry;

    /** 积分余额。 */
    private long points;
    /** 即将到期积分。 */
    private long expiringPoints;
    /** 即将到期积分失效日。 */
    private LocalDate pointsExpiringDate;

    /** 账单日。 */
    private LocalDate statementDate;
    /** 到期还款日。 */
    private LocalDate dueDate;
    /** 本期账单金额。 */
    private BigDecimal statementAmount;
    /** 最低还款额。 */
    private BigDecimal minPayment;
    /** 本期已还金额。 */
    private BigDecimal repaidAmount;
    /** 本期是否已出账。 */
    private boolean statementIssued;

    /** 卡片状态：未激活 / 正常 / 已挂失 / 已冻结。 */
    private String cardStatus = "正常";

    /** 卡片等级，如 金卡 / 白金卡。 */
    private String cardLevel = "金卡";
    /** 卡片专属权益列表。 */
    private List<String> benefits = new ArrayList<>();

    /** 年费（元）。 */
    private BigDecimal annualFee = BigDecimal.ZERO;
    /** 年费是否已（满足条件）减免。 */
    private boolean annualFeeWaived;
    /** 年费减免所需年内刷卡笔数。 */
    private int annualFeeWaiverThreshold;
    /** 本年度已刷卡笔数。 */
    private int currentYearSpendCount;

    /** 自动还款是否开启。 */
    private boolean autoRepayEnabled;
    /** 自动还款方式：全额 / 最低。 */
    private String autoRepayMode;
    /** 自动还款扣款渠道。 */
    private String autoRepayChannel;

    /** 已办理的账单分期计划。 */
    private final List<InstallmentPlan> installmentPlans = new ArrayList<>();
    /** 还款历史。 */
    private final List<RepaymentRecord> repaymentHistory = new ArrayList<>();
    /** 额度调整历史。 */
    private final List<LimitAdjustRecord> limitAdjustHistory = new ArrayList<>();
    /** 已申报争议的交易流水号（用于避免重复申报）。 */
    private final List<String> disputedTxnRefs = new ArrayList<>();

    public CardAccount(String cardNo, String holderMasked, String currency) {
        this.cardNo = cardNo;
        this.holderMasked = holderMasked;
        this.currency = currency;
    }

    public String getCardNo() {
        return cardNo;
    }

    public String getHolderMasked() {
        return holderMasked;
    }

    public String getCurrency() {
        return currency;
    }

    public BigDecimal getTotalLimit() {
        return totalLimit;
    }

    public void setTotalLimit(BigDecimal totalLimit) {
        this.totalLimit = totalLimit;
    }

    public BigDecimal getUsedAmount() {
        return usedAmount;
    }

    public void setUsedAmount(BigDecimal usedAmount) {
        this.usedAmount = usedAmount;
    }

    public BigDecimal getTempLimit() {
        return tempLimit;
    }

    public void setTempLimit(BigDecimal tempLimit) {
        this.tempLimit = tempLimit;
    }

    public LocalDate getTempLimitExpiry() {
        return tempLimitExpiry;
    }

    public void setTempLimitExpiry(LocalDate tempLimitExpiry) {
        this.tempLimitExpiry = tempLimitExpiry;
    }

    public long getPoints() {
        return points;
    }

    public void setPoints(long points) {
        this.points = points;
    }

    public long getExpiringPoints() {
        return expiringPoints;
    }

    public void setExpiringPoints(long expiringPoints) {
        this.expiringPoints = expiringPoints;
    }

    public LocalDate getPointsExpiringDate() {
        return pointsExpiringDate;
    }

    public void setPointsExpiringDate(LocalDate pointsExpiringDate) {
        this.pointsExpiringDate = pointsExpiringDate;
    }

    public LocalDate getStatementDate() {
        return statementDate;
    }

    public void setStatementDate(LocalDate statementDate) {
        this.statementDate = statementDate;
    }

    public LocalDate getDueDate() {
        return dueDate;
    }

    public void setDueDate(LocalDate dueDate) {
        this.dueDate = dueDate;
    }

    public BigDecimal getStatementAmount() {
        return statementAmount;
    }

    public void setStatementAmount(BigDecimal statementAmount) {
        this.statementAmount = statementAmount;
    }

    public BigDecimal getMinPayment() {
        return minPayment;
    }

    public void setMinPayment(BigDecimal minPayment) {
        this.minPayment = minPayment;
    }

    public BigDecimal getRepaidAmount() {
        return repaidAmount;
    }

    public void setRepaidAmount(BigDecimal repaidAmount) {
        this.repaidAmount = repaidAmount;
    }

    public boolean isStatementIssued() {
        return statementIssued;
    }

    public void setStatementIssued(boolean statementIssued) {
        this.statementIssued = statementIssued;
    }

    public String getCardStatus() {
        return cardStatus;
    }

    public void setCardStatus(String cardStatus) {
        this.cardStatus = cardStatus;
    }

    public String getCardLevel() {
        return cardLevel;
    }

    public void setCardLevel(String cardLevel) {
        this.cardLevel = cardLevel;
    }

    public List<String> getBenefits() {
        return benefits;
    }

    public void setBenefits(List<String> benefits) {
        this.benefits = benefits;
    }

    public BigDecimal getAnnualFee() {
        return annualFee;
    }

    public void setAnnualFee(BigDecimal annualFee) {
        this.annualFee = annualFee;
    }

    public boolean isAnnualFeeWaived() {
        return annualFeeWaived;
    }

    public void setAnnualFeeWaived(boolean annualFeeWaived) {
        this.annualFeeWaived = annualFeeWaived;
    }

    public int getAnnualFeeWaiverThreshold() {
        return annualFeeWaiverThreshold;
    }

    public void setAnnualFeeWaiverThreshold(int annualFeeWaiverThreshold) {
        this.annualFeeWaiverThreshold = annualFeeWaiverThreshold;
    }

    public int getCurrentYearSpendCount() {
        return currentYearSpendCount;
    }

    public void setCurrentYearSpendCount(int currentYearSpendCount) {
        this.currentYearSpendCount = currentYearSpendCount;
    }

    public boolean isAutoRepayEnabled() {
        return autoRepayEnabled;
    }

    public void setAutoRepayEnabled(boolean autoRepayEnabled) {
        this.autoRepayEnabled = autoRepayEnabled;
    }

    public String getAutoRepayMode() {
        return autoRepayMode;
    }

    public void setAutoRepayMode(String autoRepayMode) {
        this.autoRepayMode = autoRepayMode;
    }

    public String getAutoRepayChannel() {
        return autoRepayChannel;
    }

    public void setAutoRepayChannel(String autoRepayChannel) {
        this.autoRepayChannel = autoRepayChannel;
    }

    public List<InstallmentPlan> getInstallmentPlans() {
        return installmentPlans;
    }

    public List<RepaymentRecord> getRepaymentHistory() {
        return repaymentHistory;
    }

    public List<LimitAdjustRecord> getLimitAdjustHistory() {
        return limitAdjustHistory;
    }

    public List<String> getDisputedTxnRefs() {
        return disputedTxnRefs;
    }

    /** 尚需偿还金额 = 本期账单金额 - 已还金额（不小于 0）。 */
    public BigDecimal outstanding() {
        BigDecimal out = statementAmount.subtract(repaidAmount);
        return out.signum() < 0 ? BigDecimal.ZERO : out;
    }

    /** 可用额度 = 固定额度 + 临时额度 - 已用额度（不小于 0）。 */
    public BigDecimal available() {
        BigDecimal avail = totalLimit.add(tempLimit).subtract(usedAmount);
        return avail.signum() < 0 ? BigDecimal.ZERO : avail;
    }
}
