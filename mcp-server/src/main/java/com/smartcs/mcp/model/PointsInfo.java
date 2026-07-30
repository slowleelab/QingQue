package com.smartcs.mcp.model;

import java.time.LocalDate;

/**
 * 积分信息（查询类工具 query_points 的返回结构）。
 *
 * @param cardNo         完整卡号（mock 假卡号）
 * @param balance        当前可用积分余额
 * @param expiringPoints 即将到期的积分数量
 * @param expiringDate   即将到期积分的失效日期（无则为 null）
 * @param lastUpdated    积分数据更新日期
 */
public record PointsInfo(
        String cardNo,
        long balance,
        long expiringPoints,
        LocalDate expiringDate,
        LocalDate lastUpdated) {
}
