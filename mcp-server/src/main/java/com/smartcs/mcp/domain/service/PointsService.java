package com.smartcs.mcp.domain.service;

import com.smartcs.mcp.domain.CardAccount;
import com.smartcs.mcp.domain.exception.BusinessException;
import com.smartcs.mcp.domain.port.CardAccountRepository;
import com.smartcs.mcp.domain.support.Ids;
import com.smartcs.mcp.model.CardBenefitsInfo;
import com.smartcs.mcp.model.PointsInfo;
import com.smartcs.mcp.model.PointsRedeemResult;
import java.util.ArrayList;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * 积分领域服务：积分/权益查询（只读）与积分兑换（写）。
 */
@Service
public class PointsService {

    private static final Logger LOGGER = LoggerFactory.getLogger(PointsService.class);

    private final CardAccountRepository repository;
    private final IdempotentExecutor idempotent;

    public PointsService(CardAccountRepository repository, IdempotentExecutor idempotent) {
        this.repository = repository;
        this.idempotent = idempotent;
    }

    public PointsInfo queryPoints(String cardNo) {
        CardAccount acc = repository.findByCardNo(cardNo);
        return new PointsInfo(
                acc.getCardNo(),
                acc.getPoints(),
                acc.getExpiringPoints(),
                acc.getPointsExpiringDate(),
                acc.getStatementDate());
    }

    /** 查询卡片等级与专属权益（只读）。 */
    public CardBenefitsInfo queryCardBenefits(String cardNo) {
        CardAccount acc = repository.findByCardNo(cardNo);
        return new CardBenefitsInfo(acc.getCardNo(), acc.getCardLevel(), new ArrayList<>(acc.getBenefits()));
    }

    /**
     * 积分兑换（写）：消耗积分兑换指定项目，积分不足时报错。
     */
    public PointsRedeemResult redeemPoints(String cardNo, String item, long pointsCost, String idempotencyKey) {
        if (item == null || item.isBlank()) {
            throw BusinessException.invalidParam("请提供要兑换的项目名称。");
        }
        if (pointsCost <= 0) {
            throw BusinessException.invalidParam("兑换所需积分必须大于 0。");
        }
        String redeemItem = item.trim();
        return idempotent.execute("redeem_points", idempotencyKey, () ->
                repository.updateAtomically(cardNo, acc -> {
                    if (acc.getPoints() < pointsCost) {
                        throw BusinessException.pointsInsufficient(acc.getPoints());
                    }
                    acc.setPoints(acc.getPoints() - pointsCost);
                    String ref = Ids.referenceNo("RD");
                    LOGGER.info("redeem_points: card=****{}, item={}, cost={}, ref={}",
                            Ids.tail(cardNo), redeemItem, pointsCost, ref);
                    return new PointsRedeemResult(ref, acc.getCardNo(), redeemItem, pointsCost, acc.getPoints(),
                            "已受理", "已用 " + pointsCost + " 积分兑换「" + redeemItem + "」，兑换后剩余积分 "
                            + acc.getPoints() + " 分，实物权益将在 3-7 个工作日内处理。");
                }));
    }
}
