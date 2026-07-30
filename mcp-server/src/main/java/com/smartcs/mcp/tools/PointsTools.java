package com.smartcs.mcp.tools;

import com.smartcs.mcp.config.CreditCardProperties;
import com.smartcs.mcp.domain.service.PointsService;
import com.smartcs.mcp.model.CardBenefitsInfo;
import com.smartcs.mcp.model.PointsInfo;
import com.smartcs.mcp.model.PointsRedeemResult;
import org.springframework.ai.tool.annotation.Tool;
import org.springframework.ai.tool.annotation.ToolParam;
import org.springframework.stereotype.Service;

/**
 * 积分类工具：积分/权益查询（只读）与积分兑换（敏感/写）。薄适配层，业务逻辑委派 {@link PointsService}。
 */
@Service
public class PointsTools implements CreditCardTool {

    private final PointsService pointsService;
    private final CreditCardProperties properties;

    public PointsTools(PointsService pointsService, CreditCardProperties properties) {
        this.pointsService = pointsService;
        this.properties = properties;
    }

    @Tool(name = "query_points",
            description = "查询信用卡积分余额，以及本期即将到期的积分数量与失效日期。只读操作。")
    public PointsInfo queryPoints(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        return pointsService.queryPoints(no);
    }

    @Tool(name = "query_card_benefits",
            description = "查询信用卡等级及其专属权益，如机场贵宾厅、消费返现、生日礼遇等。只读操作。")
    public CardBenefitsInfo queryCardBenefits(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        return pointsService.queryCardBenefits(no);
    }

    @Tool(name = "redeem_points",
            description = "使用信用卡积分兑换指定项目（如礼品、里程、代金券）。这是敏感的写操作，须在用户明确确认后执行；"
                    + "积分余额不足时无法兑换。返回受理流水号与兑换后剩余积分。")
    public PointsRedeemResult redeemPoints(
            @ToolParam(description = "完整卡号，13-19 位数字，如 6225880012346780") String cardNo,
            @ToolParam(description = "兑换项目名称，如 100元京东E卡") String item,
            @ToolParam(description = "本次兑换需消耗的积分数，须大于 0") long pointsCost,
            @ToolParam(description = "幂等键：同一次兑换请传相同值，可避免重试导致重复受理；不填则不去重", required = false)
            String idempotencyKey) {
        String no = ToolSupport.requireCardNo(cardNo, properties.isLuhnCheck());
        return pointsService.redeemPoints(no, item, pointsCost, idempotencyKey);
    }
}
