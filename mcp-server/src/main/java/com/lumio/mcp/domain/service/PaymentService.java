package com.lumio.mcp.domain.service;

import com.lumio.mcp.config.CreditCardProperties;
import com.lumio.mcp.domain.RepaymentRecord;
import com.lumio.mcp.domain.exception.BusinessException;
import com.lumio.mcp.domain.port.CardAccountRepository;
import com.lumio.mcp.domain.support.Ids;
import com.lumio.mcp.model.AutoRepayResult;
import com.lumio.mcp.model.RepaymentHistoryInfo;
import com.lumio.mcp.model.RepaymentResult;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * 还款领域服务（写）。
 */
@Service
public class PaymentService {

    private static final Logger LOGGER = LoggerFactory.getLogger(PaymentService.class);

    private final CardAccountRepository repository;
    private final IdempotentExecutor idempotent;
    private final CreditCardProperties properties;

    public PaymentService(CardAccountRepository repository, IdempotentExecutor idempotent,
                          CreditCardProperties properties) {
        this.repository = repository;
        this.idempotent = idempotent;
        this.properties = properties;
    }

    /**
     * 办理还款（写）：还款金额计入已还，并等额冲减已用额度（不低于 0）。
     */
    public RepaymentResult repay(String cardNo, BigDecimal amount, String channel, String idempotencyKey) {
        String usedChannel = (channel == null || channel.isBlank())
                ? properties.getDefaultRepayChannel() : channel.trim();
        return idempotent.execute("repay_credit_card", idempotencyKey, () ->
                repository.updateAtomically(cardNo, acc -> {
                    acc.setRepaidAmount(acc.getRepaidAmount().add(amount));
                    BigDecimal newUsed = acc.getUsedAmount().subtract(amount);
                    acc.setUsedAmount(newUsed.signum() < 0 ? BigDecimal.ZERO : newUsed);
                    String ref = Ids.referenceNo("RP");
                    acc.getRepaymentHistory().add(new RepaymentRecord(LocalDate.now(), amount, usedChannel, "成功"));
                    LOGGER.info("repay_credit_card: card=****{}, amount={}, channel={}, ref={}",
                            Ids.tail(cardNo), amount, usedChannel, ref);
                    return new RepaymentResult(
                            ref,
                            acc.getCardNo(),
                            amount,
                            usedChannel,
                            acc.outstanding(),
                            "已受理",
                            "预计 2 小时内入账",
                            "还款 " + amount.stripTrailingZeros().toPlainString() + " 元已受理，还款后尚需偿还 "
                                    + acc.outstanding().stripTrailingZeros().toPlainString() + " 元。");
                }));
    }

    /** 查询还款历史（只读，按日期倒序）。 */
    public RepaymentHistoryInfo queryRepaymentHistory(String cardNo) {
        return repository.updateAtomically(cardNo, acc -> {
            List<RepaymentHistoryInfo.Item> items = new ArrayList<>();
            for (RepaymentRecord r : acc.getRepaymentHistory()) {
                items.add(new RepaymentHistoryInfo.Item(r.date(), r.amount(), r.channel(), r.status()));
            }
            items.sort(Comparator.comparing(RepaymentHistoryInfo.Item::date).reversed());
            return new RepaymentHistoryInfo(acc.getCardNo(), items.size(), items);
        });
    }

    /**
     * 设置/关闭自动还款（写）：mode 仅支持「全额」或「最低」。
     */
    public AutoRepayResult setAutoRepay(String cardNo, boolean enabled, String mode, String channel,
                                        String idempotencyKey) {
        String usedMode = (mode == null || mode.isBlank()) ? "全额" : mode.trim();
        if (enabled && !"全额".equals(usedMode) && !"最低".equals(usedMode)) {
            throw BusinessException.invalidParam("自动还款方式仅支持「全额」或「最低」。");
        }
        String usedChannel = (channel == null || channel.isBlank())
                ? properties.getDefaultRepayChannel() : channel.trim();
        return idempotent.execute("set_auto_repay", idempotencyKey, () ->
                repository.updateAtomically(cardNo, acc -> {
                    acc.setAutoRepayEnabled(enabled);
                    acc.setAutoRepayMode(enabled ? usedMode : null);
                    acc.setAutoRepayChannel(enabled ? usedChannel : null);
                    String ref = Ids.referenceNo("AR");
                    LOGGER.info("set_auto_repay: card=****{}, enabled={}, mode={}, ref={}",
                            Ids.tail(cardNo), enabled, enabled ? usedMode : "-", ref);
                    String message = enabled
                            ? "已开启自动还款，每期于到期还款日按「" + usedMode + "」从「" + usedChannel + "」自动扣款。"
                            : "已关闭自动还款，请留意每期账单并按时手动还款。";
                    return new AutoRepayResult(ref, acc.getCardNo(), enabled,
                            enabled ? usedMode : null, enabled ? usedChannel : null, "已设置", message);
                }));
    }
}
