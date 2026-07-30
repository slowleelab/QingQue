package com.smartcs.mcp.model;

import java.math.BigDecimal;
import java.time.LocalDate;

/**
 * 信用卡额度信息（查询类工具 query_credit_limit 的返回结构）。
 *
 * @param cardNo          完整卡号（mock 假卡号）
 * @param currency        币种
 * @param totalLimit      固定信用额度
 * @param usedAmount      已用额度
 * @param availableAmount 可用额度（含临时额度）
 * @param tempLimit       当前生效的临时额度（无则为 0）
 * @param tempLimitExpiry 临时额度到期日（无则为 null）
 */
public record CreditLimitInfo(
        String cardNo,
        String currency,
        BigDecimal totalLimit,
        BigDecimal usedAmount,
        BigDecimal availableAmount,
        BigDecimal tempLimit,
        LocalDate tempLimitExpiry) {
}
