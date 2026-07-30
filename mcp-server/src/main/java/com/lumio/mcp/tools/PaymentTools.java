package com.lumio.mcp.tools;

import com.lumio.mcp.config.CreditCardProperties;
import com.lumio.mcp.domain.service.PaymentService;
import com.lumio.mcp.model.AutoRepayResult;
import com.lumio.mcp.model.RepaymentHistoryInfo;
import com.lumio.mcp.model.RepaymentResult;
import java.math.BigDecimal;
import org.springframework.ai.tool.annotation.Tool;
import org.springframework.ai.tool.annotation.ToolParam;
import org.springframework.stereotype.Service;

/**
 * 还款工具：还款历史查询（只读）与信用卡还款、自动还款设置（敏感/写）。
 * 薄适配层，业务逻辑委派 {@link PaymentService}。
 */
@Service
public class PaymentTools implements CreditCardTool {

    private final PaymentService paymentService;
    private final CreditCardProperties properties;

    public PaymentTools(PaymentService paymentService, CreditCardProperties properties) {
        this.paymentService = paymentService;
        this.properties = properties;
    }

    @Tool(name = "repay_credit_card",
            description = "为信用卡办理还款。这是敏感的写操作，须在用户明确确认后执行。"
                    + "还款金额须大于 0，返回受理流水号、还款后尚需偿还金额与预计入账时间。")
    public RepaymentResult repayCreditCard(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo,
            @ToolParam(description = "还款金额（元），须大于 0") double amount,
            @ToolParam(description = "还款渠道，如 本人储蓄卡快捷；不填使用默认渠道", required = false) String channel,
            @ToolParam(description = "幂等键：同一次还款请传相同值，可避免重试导致重复受理；不填则不去重", required = false)
            String idempotencyKey) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        ToolSupport.requirePositiveAmount(amount);
        return paymentService.repay(no, BigDecimal.valueOf(amount), channel, idempotencyKey);
    }

    @Tool(name = "query_repayment_history",
            description = "查询信用卡历史还款记录，返回每笔还款的日期、金额、渠道与状态，按日期倒序排列。只读操作。")
    public RepaymentHistoryInfo queryRepaymentHistory(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        return paymentService.queryRepaymentHistory(no);
    }

    @Tool(name = "set_auto_repay",
            description = "开启或关闭信用卡自动还款。这是敏感的写操作，须在用户明确确认后执行。"
                    + "开启时可指定还款方式（全额/最低）与扣款渠道；返回受理流水号与设置后状态。")
    public AutoRepayResult setAutoRepay(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo,
            @ToolParam(description = "是否开启自动还款：true 开启，false 关闭") boolean enabled,
            @ToolParam(description = "还款方式：全额 或 最低；开启时不填默认全额", required = false) String mode,
            @ToolParam(description = "扣款渠道，如 本人储蓄卡快捷；不填使用默认渠道", required = false) String channel,
            @ToolParam(description = "幂等键：同一次设置请传相同值，可避免重试导致重复受理；不填则不去重", required = false)
            String idempotencyKey) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        return paymentService.setAutoRepay(no, enabled, mode, channel, idempotencyKey);
    }
}
