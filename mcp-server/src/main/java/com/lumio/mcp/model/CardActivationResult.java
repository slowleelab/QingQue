package com.lumio.mcp.model;

/**
 * 卡片激活结果（敏感/写类工具 activate_card 的返回结构）。
 *
 * @param referenceNo 受理流水号
 * @param cardNo      完整卡号（mock 假卡号）
 * @param status      激活后卡片状态，如 正常
 * @param message     面向客户的结果说明
 */
public record CardActivationResult(
        String referenceNo,
        String cardNo,
        String status,
        String message) {
}
