package com.smartcs.mcp.model;

import java.math.BigDecimal;

/**
 * 永久提额申请结果（敏感/写类工具 apply_permanent_limit 的返回结构）。
 *
 * <p>永久提额需风控审核，故受理后状态为「审核中」，不即时生效。</p>
 *
 * @param referenceNo    受理流水号
 * @param cardNo         完整卡号（mock 假卡号）
 * @param currentLimit   当前固定额度
 * @param requestedLimit 申请的目标额度
 * @param status         受理状态，如 审核中
 * @param message        面向客户的结果说明
 */
public record PermanentLimitResult(
        String referenceNo,
        String cardNo,
        BigDecimal currentLimit,
        BigDecimal requestedLimit,
        String status,
        String message) {
}
