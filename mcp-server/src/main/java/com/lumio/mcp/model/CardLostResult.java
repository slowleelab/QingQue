package com.lumio.mcp.model;

/**
 * 卡片挂失/冻结结果（敏感/写类工具 report_card_lost 的返回结构）。
 *
 * @param referenceNo   受理流水号
 * @param cardNo        完整卡号（mock 假卡号）
 * @param action        执行动作，如 挂失 / 临时冻结
 * @param status        受理状态，如 已受理
 * @param effectiveTime 生效时间说明
 * @param reissue       是否已同步发起补卡
 * @param message       面向客户的结果说明
 */
public record CardLostResult(
        String referenceNo,
        String cardNo,
        String action,
        String status,
        String effectiveTime,
        boolean reissue,
        String message) {
}
