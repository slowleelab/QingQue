package com.lumio.mcp.tools;

import com.lumio.mcp.config.CreditCardProperties;
import com.lumio.mcp.domain.service.BillingService;
import com.lumio.mcp.model.AnnualFeeInfo;
import com.lumio.mcp.model.BillDetail;
import com.lumio.mcp.model.CardBill;
import org.springframework.ai.tool.annotation.Tool;
import org.springframework.ai.tool.annotation.ToolParam;
import org.springframework.stereotype.Service;

/**
 * 账单类查询工具（只读）：账单概览、账单明细、年费信息。薄适配层，业务逻辑委派 {@link BillingService}。
 */
@Service
public class BillTools implements CreditCardTool {

    private final BillingService billingService;
    private final CreditCardProperties properties;

    public BillTools(BillingService billingService, CreditCardProperties properties) {
        this.billingService = billingService;
        this.properties = properties;
    }

    @Tool(name = "query_card_bill",
            description = "查询信用卡当期账单概览，包括本期应还金额、最低还款额、账单日、到期还款日、已还金额与尚需偿还金额。只读操作。")
    public CardBill queryCardBill(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        return billingService.queryBill(no);
    }

    @Tool(name = "query_bill_detail",
            description = "查询指定账单周期的交易明细列表，包含每笔入账日期、交易描述、商户、金额与类型，以及消费/退款合计。只读操作。")
    public BillDetail queryBillDetail(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo,
            @ToolParam(description = "账单周期，格式 yyyy-MM，如 2026-07") String billingCycle) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        String cycle = ToolSupport.requireBillingCycle(billingCycle);
        return billingService.queryBillDetail(no, cycle);
    }

    @Tool(name = "query_annual_fee",
            description = "查询信用卡年费金额、当前是否已减免，以及刷卡笔数减免政策与本年度进度。只读操作。")
    public AnnualFeeInfo queryAnnualFee(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        return billingService.queryAnnualFee(no);
    }
}
