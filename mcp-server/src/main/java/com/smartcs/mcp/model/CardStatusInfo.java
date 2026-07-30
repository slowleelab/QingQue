package com.smartcs.mcp.model;

/**
 * 卡片状态信息（查询类工具 query_card_status 的返回结构）。
 *
 * @param cardNo      完整卡号（mock 假卡号）
 * @param status      卡片状态：未激活 / 正常 / 已挂失 / 已冻结
 * @param active      是否处于可正常用卡状态
 * @param description 面向客户的状态说明
 */
public record CardStatusInfo(
        String cardNo,
        String status,
        boolean active,
        String description) {
}
