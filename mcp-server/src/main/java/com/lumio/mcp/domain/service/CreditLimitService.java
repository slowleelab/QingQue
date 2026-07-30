package com.lumio.mcp.domain.service;

import com.lumio.mcp.config.CreditCardProperties;
import com.lumio.mcp.domain.CardAccount;
import com.lumio.mcp.domain.LimitAdjustRecord;
import com.lumio.mcp.domain.exception.BusinessException;
import com.lumio.mcp.domain.port.CardAccountRepository;
import com.lumio.mcp.domain.support.Ids;
import com.lumio.mcp.model.CreditLimitInfo;
import com.lumio.mcp.model.LimitAdjustHistoryInfo;
import com.lumio.mcp.model.PermanentLimitResult;
import com.lumio.mcp.model.TempLimitResult;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * 额度领域服务：额度查询（只读）与临时额度调整（写）。
 */
@Service
public class CreditLimitService {

    private static final Logger LOGGER = LoggerFactory.getLogger(CreditLimitService.class);

    private final CardAccountRepository repository;
    private final IdempotentExecutor idempotent;
    private final CreditCardProperties properties;

    public CreditLimitService(CardAccountRepository repository, IdempotentExecutor idempotent,
                              CreditCardProperties properties) {
        this.repository = repository;
        this.idempotent = idempotent;
        this.properties = properties;
    }

    public CreditLimitInfo queryLimit(String cardNo) {
        CardAccount acc = repository.findByCardNo(cardNo);
        return new CreditLimitInfo(
                acc.getCardNo(),
                acc.getCurrency(),
                acc.getTotalLimit(),
                acc.getUsedAmount(),
                acc.available(),
                acc.getTempLimit(),
                acc.getTempLimitExpiry());
    }

    /**
     * 办理临时额度调整（写）：临时额度不得超过固定额度的配置倍数。
     */
    public TempLimitResult adjustTempLimit(String cardNo, BigDecimal target, LocalDate expiry, String idempotencyKey) {
        return idempotent.execute("adjust_temp_credit_limit", idempotencyKey, () ->
                repository.updateAtomically(cardNo, acc -> {
                    BigDecimal cap = acc.getTotalLimit().multiply(properties.getTempLimitMultiplier());
                    if (target.compareTo(cap) > 0) {
                        throw BusinessException.limitExceeded(
                                "申请的临时额度超过风控上限（不得超过固定额度的 "
                                        + properties.getTempLimitMultiplier().stripTrailingZeros().toPlainString()
                                        + " 倍），本卡上限为 " + cap.stripTrailingZeros().toPlainString() + " 元。");
                    }
                    LocalDate effective = LocalDate.now();
                    BigDecimal previousTemp = acc.getTempLimit();
                    acc.setTempLimit(target);
                    acc.setTempLimitExpiry(expiry);
                    acc.getLimitAdjustHistory().add(new LimitAdjustRecord(
                            effective, "临时", previousTemp, target, "已生效"));
                    String ref = Ids.referenceNo("TL");
                    LOGGER.info("adjust_temp_credit_limit: card=****{}, target={}, ref={}",
                            Ids.tail(cardNo), target, ref);
                    return new TempLimitResult(
                            ref,
                            acc.getCardNo(),
                            acc.getTotalLimit(),
                            target,
                            effective,
                            expiry,
                            "已生效",
                            "临时额度已调整为 " + target.stripTrailingZeros().toPlainString()
                                    + " 元，有效期至 " + expiry + "。");
                }));
    }

    /** 查询额度调整历史（只读，按日期倒序）。 */
    public LimitAdjustHistoryInfo queryLimitAdjustHistory(String cardNo) {
        return repository.updateAtomically(cardNo, acc -> {
            List<LimitAdjustHistoryInfo.Item> items = new ArrayList<>();
            for (LimitAdjustRecord r : acc.getLimitAdjustHistory()) {
                items.add(new LimitAdjustHistoryInfo.Item(r.date(), r.type(), r.fromLimit(), r.toLimit(), r.status()));
            }
            items.sort(Comparator.comparing(LimitAdjustHistoryInfo.Item::date).reversed());
            return new LimitAdjustHistoryInfo(acc.getCardNo(), items.size(), items);
        });
    }

    /**
     * 申请永久提额（写）：目标额度须高于当前固定额度；永久提额需风控审核，受理后状态为「审核中」，不即时生效。
     */
    public PermanentLimitResult applyPermanentLimit(String cardNo, BigDecimal target, String idempotencyKey) {
        return idempotent.execute("apply_permanent_limit", idempotencyKey, () ->
                repository.updateAtomically(cardNo, acc -> {
                    if (target.compareTo(acc.getTotalLimit()) <= 0) {
                        throw BusinessException.invalidParam(
                                "永久提额的目标额度须高于当前固定额度（" + acc.getTotalLimit().stripTrailingZeros().toPlainString()
                                        + " 元）。");
                    }
                    acc.getLimitAdjustHistory().add(new LimitAdjustRecord(
                            LocalDate.now(), "永久", acc.getTotalLimit(), target, "审核中"));
                    String ref = Ids.referenceNo("PL");
                    LOGGER.info("apply_permanent_limit: card=****{}, target={}, ref={}",
                            Ids.tail(cardNo), target, ref);
                    return new PermanentLimitResult(
                            ref,
                            acc.getCardNo(),
                            acc.getTotalLimit(),
                            target,
                            "审核中",
                            "永久提额申请已受理，目标额度 " + target.stripTrailingZeros().toPlainString()
                                    + " 元，将在 1-3 个工作日内完成风控审核并短信通知结果。");
                }));
    }
}
