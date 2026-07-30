package com.smartcs.mcp.domain.service;

import com.smartcs.mcp.domain.CardAccount;
import com.smartcs.mcp.domain.exception.BusinessException;
import com.smartcs.mcp.domain.port.CardAccountRepository;
import com.smartcs.mcp.domain.support.Ids;
import com.smartcs.mcp.model.CardActivationResult;
import com.smartcs.mcp.model.CardLostResult;
import com.smartcs.mcp.model.CardStatusInfo;
import com.smartcs.mcp.model.DisputeResult;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * 卡片生命周期领域服务（写）：挂失 / 临时冻结。
 */
@Service
public class CardLifecycleService {

    private static final Logger LOGGER = LoggerFactory.getLogger(CardLifecycleService.class);

    private static final String ACTION_LOST = "挂失";
    private static final String ACTION_FREEZE = "临时冻结";

    private final CardAccountRepository repository;
    private final IdempotentExecutor idempotent;

    public CardLifecycleService(CardAccountRepository repository, IdempotentExecutor idempotent) {
        this.repository = repository;
        this.idempotent = idempotent;
    }

    /**
     * 办理挂失或临时冻结（写）。
     *
     * @param action 办理类型：挂失 / 临时冻结；为空默认挂失
     * @param reissue 是否同步补卡（仅挂失时生效）
     */
    public CardLostResult reportLost(String cardNo, String action, Boolean reissue, String idempotencyKey) {
        String act = (action == null || action.isBlank()) ? ACTION_LOST : action.trim();
        if (!ACTION_LOST.equals(act) && !ACTION_FREEZE.equals(act)) {
            throw BusinessException.unsupportedAction();
        }
        boolean doReissue = Boolean.TRUE.equals(reissue) && ACTION_LOST.equals(act);
        return idempotent.execute("report_card_lost", idempotencyKey, () ->
                repository.updateAtomically(cardNo, acc -> {
                    acc.setCardStatus(ACTION_LOST.equals(act) ? "已挂失" : "已冻结");
                    String ref = Ids.referenceNo("LS");
                    LOGGER.info("report_card_lost: card=****{}, action={}, reissue={}, ref={}",
                            Ids.tail(cardNo), act, doReissue, ref);
                    String message = "已为尾号 " + Ids.tail(cardNo) + " 的信用卡办理" + act + "，原卡即时失效。"
                            + (doReissue ? "已同步发起补卡，新卡将在 3-5 个工作日寄出。" : "如需补卡请另行告知。");
                    return new CardLostResult(ref, acc.getCardNo(), act, "已受理", "即时生效", doReissue, message);
                }));
    }

    /** 查询卡片状态（只读）。 */
    public CardStatusInfo queryCardStatus(String cardNo) {
        CardAccount acc = repository.findByCardNo(cardNo);
        String status = acc.getCardStatus();
        boolean active = "正常".equals(status);
        String description = switch (status) {
            case "正常" -> "卡片状态正常，可正常用卡。";
            case "未激活" -> "卡片尚未激活，请先激活后使用。";
            case "已挂失" -> "卡片已挂失，原卡失效，如需用卡请办理补卡。";
            case "已冻结" -> "卡片已临时冻结，如需恢复请联系客服。";
            default -> "当前卡片状态为「" + status + "」。";
        };
        return new CardStatusInfo(acc.getCardNo(), status, active, description);
    }

    /**
     * 激活卡片（写）：仅「未激活」卡片可激活，激活后状态置为「正常」。
     */
    public CardActivationResult activateCard(String cardNo, String idempotencyKey) {
        return idempotent.execute("activate_card", idempotencyKey, () ->
                repository.updateAtomically(cardNo, acc -> {
                    if (!"未激活".equals(acc.getCardStatus())) {
                        throw BusinessException.alreadyActivated();
                    }
                    acc.setCardStatus("正常");
                    String ref = Ids.referenceNo("AC");
                    LOGGER.info("activate_card: card=****{}, ref={}", Ids.tail(cardNo), ref);
                    return new CardActivationResult(ref, acc.getCardNo(), "正常",
                            "尾号 " + Ids.tail(cardNo) + " 的信用卡已激活，可正常用卡。");
                }));
    }

    /**
     * 申报交易争议（写）：对指定交易流水号发起争议；同一流水号不得重复申报。
     */
    public DisputeResult reportTransactionDispute(String cardNo, String txnRef, String reason, String idempotencyKey) {
        if (txnRef == null || txnRef.isBlank()) {
            throw BusinessException.invalidParam("请提供要申报争议的交易流水号。");
        }
        String ref0 = txnRef.trim();
        String desc = (reason == null || reason.isBlank()) ? "未说明原因" : reason.trim();
        return idempotent.execute("report_transaction_dispute", idempotencyKey, () ->
                repository.updateAtomically(cardNo, acc -> {
                    if (acc.getDisputedTxnRefs().contains(ref0)) {
                        throw BusinessException.disputeAlreadyFiled(ref0);
                    }
                    acc.getDisputedTxnRefs().add(ref0);
                    String ref = Ids.referenceNo("DP");
                    LOGGER.info("report_transaction_dispute: card=****{}, txnRef={}, ref={}",
                            Ids.tail(cardNo), ref0, ref);
                    return new DisputeResult(ref, acc.getCardNo(), ref0, "已受理",
                            "交易 " + ref0 + " 的争议申报（原因：" + desc + "）已受理，我行将在 5-15 个工作日内核查并反馈结果。");
                }));
    }
}
