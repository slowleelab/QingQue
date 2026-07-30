package com.lumio.mcp.model;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

/**
 * 还款历史（查询类工具 query_repayment_history 的返回结构）。
 *
 * @param cardNo 完整卡号（mock 假卡号）
 * @param count  记录数量
 * @param items  还款记录列表（按日期倒序）
 */
public record RepaymentHistoryInfo(
        String cardNo,
        int count,
        List<Item> items) {

    /**
     * 单条还款记录。
     *
     * @param date    还款日期
     * @param amount  还款金额
     * @param channel 还款渠道
     * @param status  状态
     */
    public record Item(
            LocalDate date,
            BigDecimal amount,
            String channel,
            String status) {
    }
}
