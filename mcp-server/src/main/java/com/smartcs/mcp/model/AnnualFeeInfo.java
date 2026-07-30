package com.smartcs.mcp.model;

import java.math.BigDecimal;

/**
 * 年费信息（查询类工具 query_annual_fee 的返回结构）。
 *
 * @param cardNo            完整卡号（mock 假卡号）
 * @param annualFee         年费金额（元）
 * @param waived            当前是否已减免
 * @param waiverThreshold   年费减免所需年内刷卡笔数
 * @param currentSpendCount 本年度已刷卡笔数
 * @param waiverRule        面向客户的减免规则说明
 */
public record AnnualFeeInfo(
        String cardNo,
        BigDecimal annualFee,
        boolean waived,
        int waiverThreshold,
        int currentSpendCount,
        String waiverRule) {
}
