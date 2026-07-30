package com.lumio.mcp.model;

import java.math.BigDecimal;

/**
 * 信用卡还款结果（敏感/写类工具 repay_credit_card 的返回结构）。
 *
 * @param referenceNo      受理流水号
 * @param cardNo           完整卡号（mock 假卡号）
 * @param amount           还款金额
 * @param channel          还款渠道，如 储蓄卡快捷
 * @param outstandingAfter 还款后预计尚需偿还金额
 * @param status           受理状态，如 已受理
 * @param expectedPostTime 预计入账时间说明
 * @param message          面向客户的结果说明
 */
public record RepaymentResult(
        String referenceNo,
        String cardNo,
        BigDecimal amount,
        String channel,
        BigDecimal outstandingAfter,
        String status,
        String expectedPostTime,
        String message) {
}
