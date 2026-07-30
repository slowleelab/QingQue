package com.lumio.mcp.model;

/**
 * 交易争议申报结果（敏感/写类工具 report_transaction_dispute 的返回结构）。
 *
 * @param referenceNo 受理流水号
 * @param cardNo      完整卡号（mock 假卡号）
 * @param txnRef      被申报的交易流水号
 * @param status      受理状态，如 已受理
 * @param message     面向客户的结果说明
 */
public record DisputeResult(
        String referenceNo,
        String cardNo,
        String txnRef,
        String status,
        String message) {
}
