package com.smartcs.mcp.model;

/**
 * 分期取消结果（敏感/写类工具 cancel_installment 的返回结构）。
 *
 * @param referenceNo 受理流水号
 * @param cardNo      完整卡号（mock 假卡号）
 * @param planId      被取消的分期计划编号
 * @param status      受理状态，如 已取消
 * @param message     面向客户的结果说明
 */
public record InstallmentCancelResult(
        String referenceNo,
        String cardNo,
        String planId,
        String status,
        String message) {
}
