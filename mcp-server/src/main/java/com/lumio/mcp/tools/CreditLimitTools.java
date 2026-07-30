package com.lumio.mcp.tools;

import com.lumio.mcp.config.CreditCardProperties;
import com.lumio.mcp.domain.service.CreditLimitService;
import com.lumio.mcp.model.CreditLimitInfo;
import com.lumio.mcp.model.LimitAdjustHistoryInfo;
import com.lumio.mcp.model.PermanentLimitResult;
import com.lumio.mcp.model.TempLimitResult;
import java.math.BigDecimal;
import java.time.LocalDate;
import org.springframework.ai.tool.annotation.Tool;
import org.springframework.ai.tool.annotation.ToolParam;
import org.springframework.stereotype.Service;

/**
 * 额度类工具：额度查询/调整历史查询（只读）与临时/永久提额（敏感/写）。
 * 薄适配层，业务逻辑委派 {@link CreditLimitService}。
 */
@Service
public class CreditLimitTools implements CreditCardTool {

    private final CreditLimitService creditLimitService;
    private final CreditCardProperties properties;

    public CreditLimitTools(CreditLimitService creditLimitService, CreditCardProperties properties) {
        this.creditLimitService = creditLimitService;
        this.properties = properties;
    }

    @Tool(name = "query_credit_limit",
            description = "查询信用卡额度信息，包括固定信用额度、已用额度、可用额度、当前临时额度及其到期日。只读操作。")
    public CreditLimitInfo queryCreditLimit(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        return creditLimitService.queryLimit(no);
    }

    @Tool(name = "adjust_temp_credit_limit",
            description = "为信用卡办理临时额度调整（提额）。这是敏感的写操作，须在用户明确确认后执行；"
                    + "临时额度不得超过固定额度的风控倍数上限。返回受理流水号与生效/到期日期。")
    public TempLimitResult adjustTempCreditLimit(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo,
            @ToolParam(description = "目标临时额度金额（元），须大于 0") double targetLimit,
            @ToolParam(description = "临时额度到期日，格式 yyyy-MM-dd，如 2026-09-30；不填默认为一个月后", required = false)
            String expiryDate,
            @ToolParam(description = "幂等键：同一次办理请传相同值，可避免重试导致重复受理；不填则不去重", required = false)
            String idempotencyKey) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        ToolSupport.requirePositiveAmount(targetLimit);
        LocalDate expiry = ToolSupport.parseDateOrDefault(expiryDate, LocalDate.now().plusMonths(1));
        return creditLimitService.adjustTempLimit(no, BigDecimal.valueOf(targetLimit), expiry, idempotencyKey);
    }

    @Tool(name = "query_limit_adjust_history",
            description = "查询信用卡历史额度调整记录，包括临时提额与永久提额的办理日期、调整前后额度与状态。只读操作。")
    public LimitAdjustHistoryInfo queryLimitAdjustHistory(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        return creditLimitService.queryLimitAdjustHistory(no);
    }

    @Tool(name = "apply_permanent_limit",
            description = "为信用卡申请永久提额（固定额度上调）。这是敏感的写操作，须在用户明确确认后执行；"
                    + "目标额度须高于当前固定额度。永久提额需风控审核，受理后状态为「审核中」，不即时生效。返回受理流水号。")
    public PermanentLimitResult applyPermanentLimit(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo,
            @ToolParam(description = "申请的目标固定额度（元），须高于当前固定额度") double targetLimit,
            @ToolParam(description = "幂等键：同一次申请请传相同值，可避免重试导致重复受理；不填则不去重", required = false)
            String idempotencyKey) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        ToolSupport.requirePositiveAmount(targetLimit);
        return creditLimitService.applyPermanentLimit(no, BigDecimal.valueOf(targetLimit), idempotencyKey);
    }
}
