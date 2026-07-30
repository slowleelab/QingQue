package com.lumio.mcp.domain.service;

import com.lumio.mcp.config.CreditCardProperties;
import com.lumio.mcp.domain.CardAccount;
import com.lumio.mcp.domain.InstallmentPlan;
import com.lumio.mcp.domain.exception.BusinessException;
import com.lumio.mcp.domain.port.CardAccountRepository;
import com.lumio.mcp.domain.support.Ids;
import com.lumio.mcp.model.InstallmentCancelResult;
import com.lumio.mcp.model.InstallmentOffer;
import com.lumio.mcp.model.InstallmentResult;
import com.lumio.mcp.model.InstallmentStatusInfo;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * 账单分期领域服务：分期方案查询（只读）与账单分期办理（写）。
 *
 * <p>支持期数与每期费率来自 {@link CreditCardProperties#getInstallmentFeeRates()}（可配置）。</p>
 */
@Service
public class InstallmentService {

    private static final Logger LOGGER = LoggerFactory.getLogger(InstallmentService.class);

    private final CardAccountRepository repository;
    private final IdempotentExecutor idempotent;
    private final CreditCardProperties properties;

    public InstallmentService(CardAccountRepository repository, IdempotentExecutor idempotent,
                              CreditCardProperties properties) {
        this.repository = repository;
        this.idempotent = idempotent;
        this.properties = properties;
    }

    public InstallmentOffer queryOffer(String cardNo) {
        CardAccount acc = repository.findByCardNo(cardNo);
        BigDecimal eligible = acc.outstanding();
        List<InstallmentOffer.Plan> plans = new ArrayList<>();
        for (Map.Entry<Integer, BigDecimal> entry : properties.getInstallmentFeeRates().entrySet()) {
            plans.add(buildPlan(eligible, entry.getKey(), entry.getValue()));
        }
        return new InstallmentOffer(cardNo, eligible, plans);
    }

    /**
     * 办理账单分期（写）：期数须受支持，分期金额不得超过当期尚需偿还金额。
     */
    public InstallmentResult applyInstallment(String cardNo, BigDecimal amount, int periods, String idempotencyKey) {
        BigDecimal rate = properties.getInstallmentFeeRates().get(periods);
        if (rate == null) {
            throw BusinessException.unsupportedPeriod();
        }
        return idempotent.execute("apply_bill_installment", idempotencyKey, () ->
                repository.updateAtomically(cardNo, acc -> {
                    if (amount.compareTo(acc.outstanding()) > 0) {
                        throw BusinessException.amountExceedsOutstanding(
                                acc.outstanding().stripTrailingZeros().toPlainString());
                    }
                    InstallmentOffer.Plan plan = buildPlan(amount, periods, rate);
                    String ref = Ids.referenceNo("IN");
                    acc.getInstallmentPlans().add(new InstallmentPlan(
                            ref, amount, periods, periods, plan.perPeriodTotal(), "分期中"));
                    LOGGER.info("apply_bill_installment: card=****{}, amount={}, periods={}, ref={}",
                            Ids.tail(cardNo), amount, periods, ref);
                    return new InstallmentResult(
                            ref,
                            acc.getCardNo(),
                            amount,
                            periods,
                            plan.perPeriodTotal(),
                            plan.totalFee(),
                            "已受理",
                            "账单分期已受理，共 " + periods + " 期，每期应还 " + plan.perPeriodTotal().toPlainString()
                                    + " 元，将于下期账单起分月计入。");
                }));
    }

    /** 查询已办理分期计划的状态（只读）。 */
    public InstallmentStatusInfo queryInstallmentStatus(String cardNo) {
        CardAccount acc = repository.findByCardNo(cardNo);
        List<InstallmentStatusInfo.Plan> plans = new ArrayList<>();
        for (InstallmentPlan p : acc.getInstallmentPlans()) {
            plans.add(new InstallmentStatusInfo.Plan(
                    p.planId(), p.principal(), p.periods(), p.remainingPeriods(), p.perPeriodAmount(), p.status()));
        }
        return new InstallmentStatusInfo(acc.getCardNo(), plans.size(), plans);
    }

    /**
     * 取消账单分期（写）：仅「分期中」计划可取消；未找到或状态不可取消时报错。
     */
    public InstallmentCancelResult cancelInstallment(String cardNo, String planId, String idempotencyKey) {
        if (planId == null || planId.isBlank()) {
            throw BusinessException.invalidParam("请提供要取消的分期计划编号。");
        }
        String pid = planId.trim();
        return idempotent.execute("cancel_installment", idempotencyKey, () ->
                repository.updateAtomically(cardNo, acc -> {
                    List<InstallmentPlan> plans = acc.getInstallmentPlans();
                    int idx = -1;
                    for (int i = 0; i < plans.size(); i++) {
                        if (plans.get(i).planId().equals(pid) && "分期中".equals(plans.get(i).status())) {
                            idx = i;
                            break;
                        }
                    }
                    if (idx < 0) {
                        throw BusinessException.installmentNotFound(pid);
                    }
                    plans.set(idx, plans.get(idx).withStatus("已取消"));
                    String ref = Ids.referenceNo("CI");
                    LOGGER.info("cancel_installment: card=****{}, planId={}, ref={}", Ids.tail(cardNo), pid, ref);
                    return new InstallmentCancelResult(ref, acc.getCardNo(), pid, "已取消",
                            "分期计划 " + pid + " 已取消，剩余未出账本金将并入下期账单，已计入的手续费不予退还。");
                }));
    }

    private static InstallmentOffer.Plan buildPlan(BigDecimal principal, int periods, BigDecimal monthlyRate) {
        BigDecimal perPrincipal = principal.divide(BigDecimal.valueOf(periods), 2, RoundingMode.HALF_UP);
        BigDecimal perFee = principal.multiply(monthlyRate).setScale(2, RoundingMode.HALF_UP);
        BigDecimal perTotal = perPrincipal.add(perFee);
        BigDecimal totalFee = perFee.multiply(BigDecimal.valueOf(periods));
        BigDecimal totalRate = monthlyRate.multiply(BigDecimal.valueOf(periods)).setScale(4, RoundingMode.HALF_UP);
        return new InstallmentOffer.Plan(periods, totalRate, perPrincipal, perFee, perTotal, totalFee);
    }
}
