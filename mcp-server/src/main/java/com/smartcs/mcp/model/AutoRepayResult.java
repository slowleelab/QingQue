package com.smartcs.mcp.model;

/**
 * 自动还款设置结果（敏感/写类工具 set_auto_repay 的返回结构）。
 *
 * @param referenceNo 受理流水号
 * @param cardNo      完整卡号（mock 假卡号）
 * @param enabled     是否开启自动还款
 * @param mode        自动还款方式：全额 / 最低
 * @param channel     自动还款扣款渠道
 * @param status      受理状态，如 已设置
 * @param message     面向客户的结果说明
 */
public record AutoRepayResult(
        String referenceNo,
        String cardNo,
        boolean enabled,
        String mode,
        String channel,
        String status,
        String message) {
}
