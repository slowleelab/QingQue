package com.smartcs.mcp.model;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

/**
 * 额度调整历史（查询类工具 query_limit_adjust_history 的返回结构）。
 *
 * @param cardNo 完整卡号（mock 假卡号）
 * @param count  记录数量
 * @param items  额度调整记录列表（按日期倒序）
 */
public record LimitAdjustHistoryInfo(
        String cardNo,
        int count,
        List<Item> items) {

    /**
     * 单条额度调整记录。
     *
     * @param date      办理日期
     * @param type      调整类型：临时 / 永久
     * @param fromLimit 调整前额度
     * @param toLimit   调整后（目标）额度
     * @param status    状态
     */
    public record Item(
            LocalDate date,
            String type,
            BigDecimal fromLimit,
            BigDecimal toLimit,
            String status) {
    }
}
