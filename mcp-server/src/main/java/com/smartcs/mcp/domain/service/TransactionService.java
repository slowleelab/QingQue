package com.smartcs.mcp.domain.service;

import com.smartcs.mcp.domain.TransactionRecord;
import com.smartcs.mcp.domain.port.CardAccountRepository;
import com.smartcs.mcp.model.TransactionPage;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import org.springframework.stereotype.Service;

/**
 * 交易流水领域服务（只读）。
 */
@Service
public class TransactionService {

    private final CardAccountRepository repository;

    public TransactionService(CardAccountRepository repository) {
        this.repository = repository;
    }

    public TransactionPage queryTransactions(String cardNo, LocalDate from, LocalDate to) {
        List<TransactionRecord> records = repository.transactionsBetween(cardNo, from, to);
        List<TransactionPage.Txn> txns = new ArrayList<>();
        for (TransactionRecord r : records) {
            txns.add(new TransactionPage.Txn(
                    r.txnTime(), r.description(), r.merchant(), r.amount(), r.type(), r.status()));
        }
        return new TransactionPage(cardNo, from, to, txns.size(), txns);
    }
}
