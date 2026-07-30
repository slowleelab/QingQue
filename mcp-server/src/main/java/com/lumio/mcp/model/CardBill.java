package com.lumio.mcp.model;

import java.math.BigDecimal;
import java.time.LocalDate;

/**
 * 信用卡当期账单概览（查询类工具 query_card_bill 的返回结构）。
 *
 * @param cardNo            完整卡号（mock 假卡号）
 * @param currency          币种，如 CNY
 * @param statementIssued   本期是否已出账
 * @param statementDate     账单日
 * @param dueDate           到期还款日
 * @param statementAmount   本期账单金额（应还总额）
 * @param minPayment        最低还款额
 * @param repaidAmount      本期已还金额
 * @param outstandingAmount 当前尚需偿还金额
 * @param status            账单状态描述，如「已出账，未还清」
 */
public record CardBill(
        String cardNo,
        String currency,
        boolean statementIssued,
        LocalDate statementDate,
        LocalDate dueDate,
        BigDecimal statementAmount,
        BigDecimal minPayment,
        BigDecimal repaidAmount,
        BigDecimal outstandingAmount,
        String status) {
}
