package com.lumio.mcp.domain;

import java.math.BigDecimal;
import java.time.LocalDate;

/**
 * 还款历史记录（账户维度状态，<b>仅演示数据</b>）。
 *
 * @param date    还款日期
 * @param amount  还款金额
 * @param channel 还款渠道
 * @param status  状态，如 成功 / 处理中
 */
public record RepaymentRecord(
        LocalDate date,
        BigDecimal amount,
        String channel,
        String status) {
}
