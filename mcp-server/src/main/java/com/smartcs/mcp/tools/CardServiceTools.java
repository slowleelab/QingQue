package com.smartcs.mcp.tools;

import com.smartcs.mcp.config.CreditCardProperties;
import com.smartcs.mcp.domain.service.CardLifecycleService;
import com.smartcs.mcp.model.CardActivationResult;
import com.smartcs.mcp.model.CardLostResult;
import com.smartcs.mcp.model.CardStatusInfo;
import com.smartcs.mcp.model.DisputeResult;
import org.springframework.ai.tool.annotation.Tool;
import org.springframework.ai.tool.annotation.ToolParam;
import org.springframework.stereotype.Service;

/**
 * 卡片服务工具：卡片状态查询（只读）与卡片挂失/冻结、开卡激活、交易争议申报（敏感/写）。
 * 薄适配层，业务逻辑委派 {@link CardLifecycleService}。
 */
@Service
public class CardServiceTools implements CreditCardTool {

    private final CardLifecycleService cardLifecycleService;
    private final CreditCardProperties properties;

    public CardServiceTools(CardLifecycleService cardLifecycleService, CreditCardProperties properties) {
        this.cardLifecycleService = cardLifecycleService;
        this.properties = properties;
    }

    @Tool(name = "report_card_lost",
            description = "为信用卡办理挂失或临时冻结。这是敏感的写操作，须在用户明确确认后执行。"
                    + "挂失后原卡立即失效，可选择是否同步补卡；返回受理流水号与生效时间。")
    public CardLostResult reportCardLost(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo,
            @ToolParam(description = "办理类型：挂失 或 临时冻结；不填默认挂失", required = false) String action,
            @ToolParam(description = "是否同步申请补卡，默认 false", required = false) Boolean reissue,
            @ToolParam(description = "幂等键：同一次办理请传相同值，可避免重试导致重复受理；不填则不去重", required = false)
            String idempotencyKey) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        return cardLifecycleService.reportLost(no, action, reissue, idempotencyKey);
    }

    @Tool(name = "query_card_status",
            description = "查询信用卡当前状态（未激活/正常/已挂失/已冻结）及是否可正常用卡，并给出状态说明。只读操作。")
    public CardStatusInfo queryCardStatus(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        return cardLifecycleService.queryCardStatus(no);
    }

    @Tool(name = "activate_card",
            description = "激活一张新申请的信用卡。这是敏感的写操作，须在用户明确确认后执行。"
                    + "仅「未激活」状态的卡片可激活，激活后即可正常用卡；返回受理流水号与激活后状态。")
    public CardActivationResult activateCard(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo,
            @ToolParam(description = "幂等键：同一次激活请传相同值，可避免重试导致重复受理；不填则不去重", required = false)
            String idempotencyKey) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        return cardLifecycleService.activateCard(no, idempotencyKey);
    }

    @Tool(name = "report_transaction_dispute",
            description = "对信用卡某笔交易发起争议申报（如未授权交易、重复扣款、金额有误）。这是敏感的写操作，须在用户明确确认后执行。"
                    + "同一笔交易不得重复申报；返回受理流水号与后续处理说明。")
    public DisputeResult reportTransactionDispute(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo,
            @ToolParam(description = "有争议的交易流水号（来自 query_transactions）") String txnRef,
            @ToolParam(description = "争议原因，如 未授权交易 / 重复扣款；不填则记为未说明原因", required = false) String reason,
            @ToolParam(description = "幂等键：同一次申报请传相同值，可避免重试导致重复受理；不填则不去重", required = false)
            String idempotencyKey) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        return cardLifecycleService.reportTransactionDispute(no, txnRef, reason, idempotencyKey);
    }
}
