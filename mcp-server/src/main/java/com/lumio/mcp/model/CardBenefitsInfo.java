package com.lumio.mcp.model;

import java.util.List;

/**
 * 卡片权益信息（查询类工具 query_card_benefits 的返回结构）。
 *
 * @param cardNo    完整卡号（mock 假卡号）
 * @param cardLevel 卡片等级，如 金卡 / 白金卡
 * @param benefits  权益列表
 */
public record CardBenefitsInfo(
        String cardNo,
        String cardLevel,
        List<String> benefits) {
}
