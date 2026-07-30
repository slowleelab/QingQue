package com.lumio.mcp.domain;

import java.math.BigDecimal;
import java.time.LocalDate;

/**
 * 额度调整历史记录（账户维度状态，<b>仅演示数据</b>）。
 *
 * @param date      办理日期
 * @param type      调整类型：临时 / 永久
 * @param fromLimit 调整前额度
 * @param toLimit   调整后（目标）额度
 * @param status    状态，如 已生效 / 审核中 / 已拒绝
 */
public record LimitAdjustRecord(
        LocalDate date,
        String type,
        BigDecimal fromLimit,
        BigDecimal toLimit,
        String status) {
}
