package com.smartcs.mcp.domain;

import java.math.BigDecimal;

/**
 * 已办理的账单分期计划（账户维度状态，<b>仅演示数据</b>）。
 *
 * @param planId          分期计划编号
 * @param principal       分期本金
 * @param periods         总期数
 * @param remainingPeriods 剩余未还期数
 * @param perPeriodAmount 每期应还合计
 * @param status          计划状态：分期中 / 已结清 / 已取消
 */
public record InstallmentPlan(
        String planId,
        BigDecimal principal,
        int periods,
        int remainingPeriods,
        BigDecimal perPeriodAmount,
        String status) {

    /** 返回一个仅变更状态的副本（记录不可变，取消/结清时用）。 */
    public InstallmentPlan withStatus(String newStatus) {
        return new InstallmentPlan(planId, principal, periods, remainingPeriods, perPeriodAmount, newStatus);
    }
}
