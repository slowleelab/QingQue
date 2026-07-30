package com.smartcs.mcp.model;

import java.math.BigDecimal;
import java.util.List;

/**
 * 账单分期可选方案（查询类工具 query_installment_offer 的返回结构）。
 *
 * @param cardNo         完整卡号（mock 假卡号）
 * @param eligibleAmount 可分期本金上限
 * @param plans          可选方案列表
 */
public record InstallmentOffer(
        String cardNo,
        BigDecimal eligibleAmount,
        List<Plan> plans) {

    /**
     * 单个分期方案。
     *
     * @param periods           期数
     * @param totalFeeRate       总手续费率（如 0.023 表示 2.3%）
     * @param perPeriodPrincipal 每期本金
     * @param perPeriodFee       每期手续费
     * @param perPeriodTotal     每期应还合计
     * @param totalFee           手续费合计
     */
    public record Plan(
            int periods,
            BigDecimal totalFeeRate,
            BigDecimal perPeriodPrincipal,
            BigDecimal perPeriodFee,
            BigDecimal perPeriodTotal,
            BigDecimal totalFee) {
    }
}
