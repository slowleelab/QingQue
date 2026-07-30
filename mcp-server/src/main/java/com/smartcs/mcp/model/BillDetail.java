package com.smartcs.mcp.model;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

/**
 * 账单交易明细（查询类工具 query_bill_detail 的返回结构）。
 *
 * @param cardNo       完整卡号（mock 假卡号）
 * @param billingCycle 账单周期，如 2026-06
 * @param totalSpend   本周期消费合计
 * @param totalRefund  本周期退款合计
 * @param count        明细条数
 * @param items        明细列表
 */
public record BillDetail(
        String cardNo,
        String billingCycle,
        BigDecimal totalSpend,
        BigDecimal totalRefund,
        int count,
        List<Item> items) {

    /**
     * 单条账单明细。
     *
     * @param postDate    入账日期
     * @param description 交易描述
     * @param merchant    商户名称
     * @param amount      金额（正数为消费，负数为退款/入账）
     * @param type        交易类型，如 消费 / 退款
     */
    public record Item(
            LocalDate postDate,
            String description,
            String merchant,
            BigDecimal amount,
            String type) {
    }
}
