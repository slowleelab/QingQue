package com.lumio.mcp.model;

import java.math.BigDecimal;

/**
 * 账单分期办理结果（敏感/写类工具 apply_bill_installment 的返回结构）。
 *
 * @param referenceNo    受理流水号
 * @param cardNo         完整卡号（mock 假卡号）
 * @param amount         分期本金
 * @param periods        期数
 * @param perPeriodTotal 每期应还合计
 * @param totalFee       手续费合计
 * @param status         受理状态，如 已受理
 * @param message        面向客户的结果说明
 */
public record InstallmentResult(
        String referenceNo,
        String cardNo,
        BigDecimal amount,
        int periods,
        BigDecimal perPeriodTotal,
        BigDecimal totalFee,
        String status,
        String message) {
}
