package com.smartcs.mcp.tools;

import com.smartcs.mcp.config.CreditCardProperties;
import com.smartcs.mcp.domain.exception.BusinessException;
import com.smartcs.mcp.domain.service.TransactionService;
import com.smartcs.mcp.model.TransactionPage;
import java.time.LocalDate;
import org.springframework.ai.tool.annotation.Tool;
import org.springframework.ai.tool.annotation.ToolParam;
import org.springframework.stereotype.Service;

/**
 * 交易流水查询工具（只读）。薄适配层，业务逻辑委派 {@link TransactionService}。
 */
@Service
public class TransactionTools implements CreditCardTool {

    private final TransactionService transactionService;
    private final CreditCardProperties properties;

    public TransactionTools(TransactionService transactionService, CreditCardProperties properties) {
        this.transactionService = transactionService;
        this.properties = properties;
    }

    @Tool(name = "query_transactions",
            description = "查询信用卡近期交易流水，可按起止日期筛选，返回每笔交易的时间、描述、商户、金额、类型与状态。"
                    + "不传日期时默认查询最近 30 天。只读操作。")
    public TransactionPage queryTransactions(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo,
            @ToolParam(description = "起始日期，格式 yyyy-MM-dd；不填默认最近 30 天", required = false) String fromDate,
            @ToolParam(description = "截止日期，格式 yyyy-MM-dd；不填默认为今天", required = false) String toDate) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        LocalDate to = ToolSupport.parseDateOrDefault(toDate, LocalDate.now());
        LocalDate from = ToolSupport.parseDateOrDefault(fromDate, to.minusDays(30));
        if (from.isAfter(to)) {
            throw BusinessException.invalidParam("起始日期不能晚于截止日期。");
        }
        return transactionService.queryTransactions(no, from, to);
    }
}
