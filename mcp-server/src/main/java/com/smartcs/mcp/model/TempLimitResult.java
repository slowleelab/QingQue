package com.smartcs.mcp.model;

import java.math.BigDecimal;
import java.time.LocalDate;

/**
 * 临时额度调整结果（敏感/写类工具 adjust_temp_credit_limit 的返回结构）。
 *
 * @param referenceNo   受理流水号
 * @param cardNo        完整卡号（mock 假卡号）
 * @param originalLimit 固定信用额度
 * @param newTempLimit  调整后的临时额度
 * @param effectiveFrom 生效日期
 * @param expiry        临时额度到期日
 * @param status        受理状态，如 已生效
 * @param message       面向客户的结果说明
 */
public record TempLimitResult(
        String referenceNo,
        String cardNo,
        BigDecimal originalLimit,
        BigDecimal newTempLimit,
        LocalDate effectiveFrom,
        LocalDate expiry,
        String status,
        String message) {
}
