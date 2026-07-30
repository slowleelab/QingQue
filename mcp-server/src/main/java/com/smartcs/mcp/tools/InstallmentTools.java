package com.smartcs.mcp.tools;

import com.smartcs.mcp.config.CreditCardProperties;
import com.smartcs.mcp.domain.service.InstallmentService;
import com.smartcs.mcp.model.InstallmentCancelResult;
import com.smartcs.mcp.model.InstallmentOffer;
import com.smartcs.mcp.model.InstallmentResult;
import com.smartcs.mcp.model.InstallmentStatusInfo;
import java.math.BigDecimal;
import org.springframework.ai.tool.annotation.Tool;
import org.springframework.ai.tool.annotation.ToolParam;
import org.springframework.stereotype.Service;

/**
 * 账单分期工具：分期方案/在办分期查询（只读）与账单分期办理、分期取消（敏感/写）。
 * 薄适配层，业务逻辑委派 {@link InstallmentService}。
 */
@Service
public class InstallmentTools implements CreditCardTool {

    private final InstallmentService installmentService;
    private final CreditCardProperties properties;

    public InstallmentTools(InstallmentService installmentService, CreditCardProperties properties) {
        this.installmentService = installmentService;
        this.properties = properties;
    }

    @Tool(name = "query_installment_offer",
            description = "查询信用卡账单分期的可选方案，返回可分期本金上限以及各期数（如 3/6/12/24 期）对应的费率、"
                    + "每期本金、每期手续费与每期应还合计。只读操作。")
    public InstallmentOffer queryInstallmentOffer(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        return installmentService.queryOffer(no);
    }

    @Tool(name = "apply_bill_installment",
            description = "为信用卡办理账单分期。这是敏感的写操作，须在用户明确确认后执行。"
                    + "期数仅支持受支持的档位（如 3/6/12/24 期），分期金额不得超过当期尚需偿还金额。返回受理流水号与每期应还金额。")
    public InstallmentResult applyBillInstallment(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo,
            @ToolParam(description = "分期本金金额（元），须大于 0 且不超过当期尚需偿还金额") double amount,
            @ToolParam(description = "分期期数，仅支持 3、6、12、24") int periods,
            @ToolParam(description = "幂等键：同一次办理请传相同值，可避免重试导致重复受理；不填则不去重", required = false)
            String idempotencyKey) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        ToolSupport.requirePositiveAmount(amount);
        return installmentService.applyInstallment(no, BigDecimal.valueOf(amount), periods, idempotencyKey);
    }

    @Tool(name = "query_installment_status",
            description = "查询信用卡当前在办及历史账单分期计划，返回每笔分期的计划号、本金、总期数、剩余期数、"
                    + "每期应还金额与状态（分期中/已结清/已取消）。只读操作。")
    public InstallmentStatusInfo queryInstallmentStatus(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        return installmentService.queryInstallmentStatus(no);
    }

    @Tool(name = "cancel_installment",
            description = "取消信用卡一笔在办的账单分期。这是敏感的写操作，须在用户明确确认后执行。"
                    + "仅状态为分期中的计划可取消；返回受理流水号与取消后状态。")
    public InstallmentCancelResult cancelInstallment(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo,
            @ToolParam(description = "待取消的分期计划号（来自 query_installment_status）") String planId,
            @ToolParam(description = "幂等键：同一次取消请传相同值，可避免重试导致重复受理；不填则不去重", required = false)
            String idempotencyKey) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        return installmentService.cancelInstallment(no, planId, idempotencyKey);
    }
}
