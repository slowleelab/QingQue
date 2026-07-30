package com.smartcs.mcp.domain;

import java.math.BigDecimal;
import java.time.LocalDate;

/**
 * 领域层交易记录（账户维度的原始流水，独立于对外返回 DTO）。
 *
 * @param postDate    入账日期（用于账单周期/日期区间筛选）
 * @param txnTime     交易时间（ISO-8601 展示串）
 * @param description 交易描述
 * @param merchant    商户名称
 * @param amount      金额（正数为消费，负数为退款/还款）
 * @param type        交易类型，如 消费 / 退款 / 还款
 * @param status      交易状态，如 成功 / 处理中
 */
public record TransactionRecord(
        LocalDate postDate,
        String txnTime,
        String description,
        String merchant,
        BigDecimal amount,
        String type,
        String status) {
}
