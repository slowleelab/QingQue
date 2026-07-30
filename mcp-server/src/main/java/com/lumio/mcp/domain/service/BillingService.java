package com.lumio.mcp.domain.service;

import com.lumio.mcp.domain.CardAccount;
import com.lumio.mcp.domain.TransactionRecord;
import com.lumio.mcp.domain.port.CardAccountRepository;
import com.lumio.mcp.model.AnnualFeeInfo;
import com.lumio.mcp.model.BillDetail;
import com.lumio.mcp.model.CardBill;
import java.math.BigDecimal;
import java.time.YearMonth;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import org.springframework.stereotype.Service;

/**
 * 账单领域服务（只读）：账单概览与账单明细。
 */
@Service
public class BillingService {

    private final CardAccountRepository repository;

    public BillingService(CardAccountRepository repository) {
        this.repository = repository;
    }

    /** 查询当期账单概览。 */
    public CardBill queryBill(String cardNo) {
        CardAccount acc = repository.findByCardNo(cardNo);
        String status = !acc.isStatementIssued()
                ? "本期未出账"
                : (acc.outstanding().signum() == 0 ? "已出账，已还清" : "已出账，未还清");
        return new CardBill(
                acc.getCardNo(),
                acc.getCurrency(),
                acc.isStatementIssued(),
                acc.getStatementDate(),
                acc.getDueDate(),
                acc.getStatementAmount(),
                acc.getMinPayment(),
                acc.getRepaidAmount(),
                acc.outstanding(),
                status);
    }

    /** 按账单周期（yyyy-MM）汇总交易明细。 */
    public BillDetail queryBillDetail(String cardNo, String billingCycle) {
        YearMonth ym = YearMonth.parse(billingCycle);
        List<TransactionRecord> records =
                repository.transactionsBetween(cardNo, ym.atDay(1), ym.atEndOfMonth());
        List<BillDetail.Item> items = new ArrayList<>();
        BigDecimal spend = BigDecimal.ZERO;
        BigDecimal refund = BigDecimal.ZERO;
        for (TransactionRecord r : records) {
            items.add(new BillDetail.Item(r.postDate(), r.description(), r.merchant(), r.amount(), r.type()));
            if (r.amount().signum() >= 0) {
                spend = spend.add(r.amount());
            } else {
                refund = refund.add(r.amount().abs());
            }
        }
        items.sort(Comparator.comparing(BillDetail.Item::postDate).reversed());
        return new BillDetail(cardNo, billingCycle, spend, refund, items.size(), items);
    }

    /** 查询年费信息与减免进度（只读）。 */
    public AnnualFeeInfo queryAnnualFee(String cardNo) {
        CardAccount acc = repository.findByCardNo(cardNo);
        int threshold = acc.getAnnualFeeWaiverThreshold();
        int spent = acc.getCurrentYearSpendCount();
        boolean waived = acc.isAnnualFeeWaived() || (threshold > 0 && spent >= threshold);
        String rule;
        if (threshold <= 0) {
            rule = "该卡年费为 " + acc.getAnnualFee().stripTrailingZeros().toPlainString() + " 元，无刷卡减免政策。";
        } else if (waived) {
            rule = "本年度刷卡已满 " + threshold + " 笔，年费已减免。";
        } else {
            rule = "本年度刷卡满 " + threshold + " 笔可减免年费，当前已刷 " + spent + " 笔，还差 "
                    + (threshold - spent) + " 笔。";
        }
        return new AnnualFeeInfo(acc.getCardNo(), acc.getAnnualFee(), waived, threshold, spent, rule);
    }
}
