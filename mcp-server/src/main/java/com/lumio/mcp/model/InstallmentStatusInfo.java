package com.lumio.mcp.model;

import java.math.BigDecimal;
import java.util.List;

/**
 * 分期计划状态（查询类工具 query_installment_status 的返回结构）。
 *
 * @param cardNo 完整卡号（mock 假卡号）
 * @param count  分期计划数量
 * @param plans  分期计划列表
 */
public record InstallmentStatusInfo(
        String cardNo,
        int count,
        List<Plan> plans) {

    /**
     * 单条分期计划。
     *
     * @param planId           分期计划编号
     * @param principal        分期本金
     * @param periods          总期数
     * @param remainingPeriods 剩余未还期数
     * @param perPeriodAmount  每期应还合计
     * @param status           计划状态
     */
    public record Plan(
            String planId,
            BigDecimal principal,
            int periods,
            int remainingPeriods,
            BigDecimal perPeriodAmount,
            String status) {
    }
}
