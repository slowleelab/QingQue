package com.smartcs.mcp.model;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

/**
 * 近期交易流水分页结果（查询类工具 query_transactions 的返回结构）。
 *
 * @param cardNo       完整卡号（mock 假卡号）
 * @param from         查询起始日期
 * @param to           查询截止日期
 * @param count        返回条数
 * @param transactions 交易列表（按时间倒序）
 */
public record TransactionPage(
        String cardNo,
        LocalDate from,
        LocalDate to,
        int count,
        List<Txn> transactions) {

    /**
     * 单笔交易。
     *
     * @param txnTime     交易时间（ISO-8601）
     * @param description 交易描述
     * @param merchant    商户名称
     * @param amount      金额（正数为消费，负数为退款/还款）
     * @param type        交易类型，如 消费 / 退款 / 还款
     * @param status      交易状态，如 成功 / 处理中
     */
    public record Txn(
            String txnTime,
            String description,
            String merchant,
            BigDecimal amount,
            String type,
            String status) {
    }
}
