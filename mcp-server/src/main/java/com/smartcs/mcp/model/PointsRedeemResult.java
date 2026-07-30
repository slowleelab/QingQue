package com.smartcs.mcp.model;

/**
 * 积分兑换结果（敏感/写类工具 redeem_points 的返回结构）。
 *
 * @param referenceNo       受理流水号
 * @param cardNo            完整卡号（mock 假卡号）
 * @param item              兑换项目
 * @param pointsCost        本次消耗积分
 * @param pointsBalanceAfter 兑换后剩余积分
 * @param status            受理状态，如 已受理
 * @param message           面向客户的结果说明
 */
public record PointsRedeemResult(
        String referenceNo,
        String cardNo,
        String item,
        long pointsCost,
        long pointsBalanceAfter,
        String status,
        String message) {
}
