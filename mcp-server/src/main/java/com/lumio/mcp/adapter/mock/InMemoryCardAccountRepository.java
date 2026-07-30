package com.lumio.mcp.adapter.mock;

import com.lumio.mcp.domain.CardAccount;
import com.lumio.mcp.domain.TransactionRecord;
import com.lumio.mcp.domain.exception.BusinessException;
import com.lumio.mcp.domain.port.CardAccountRepository;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.locks.ReentrantLock;
import java.util.function.Function;
import org.springframework.stereotype.Repository;

/**
 * 内存实现的信用卡账户仓储适配器（六边形架构 driven adapter）。
 *
 * <p><b>安全红线：本适配器不连接任何真实银行核心系统</b>，所有数据均为构造的演示数据。
 * 未来接入真实核心系统时，只需另写一个实现 {@link CardAccountRepository} 的适配器替换本类，
 * 领域服务与工具层无需改动。</p>
 *
 * <p>并发安全：{@link #updateAtomically} 对每个账户持有独立 {@link ReentrantLock}，
 * 保证还款、提额、分期等「读—改」序列在账户维度串行执行，消除竞态。</p>
 */
@Repository
public class InMemoryCardAccountRepository implements CardAccountRepository {

    private final Map<String, CardAccount> accounts = new ConcurrentHashMap<>();
    private final Map<String, List<TransactionRecord>> transactions = new ConcurrentHashMap<>();
    private final Map<String, ReentrantLock> locks = new ConcurrentHashMap<>();

    /**
     * 载入一张演示账户及其交易流水。由 {@link MockDataSeeder} 调用。
     *
     * @param account 账户实体
     * @param txns    该账户的交易记录（可为空）
     */
    public void save(CardAccount account, List<TransactionRecord> txns) {
        accounts.put(account.getCardNo(), account);
        transactions.put(account.getCardNo(), new ArrayList<>(txns));
    }

    @Override
    public CardAccount findByCardNo(String cardNo) {
        CardAccount account = accounts.get(cardNo);
        if (account == null) {
            throw BusinessException.accountNotFound(cardNo);
        }
        return account;
    }

    @Override
    public <R> R updateAtomically(String cardNo, Function<CardAccount, R> mutation) {
        CardAccount account = findByCardNo(cardNo);
        ReentrantLock lock = locks.computeIfAbsent(cardNo, k -> new ReentrantLock());
        lock.lock();
        try {
            return mutation.apply(account);
        } finally {
            lock.unlock();
        }
    }

    @Override
    public List<TransactionRecord> transactionsBetween(String cardNo, LocalDate from, LocalDate to) {
        findByCardNo(cardNo); // 触发账户存在性校验
        List<TransactionRecord> all = transactions.getOrDefault(cardNo, List.of());
        List<TransactionRecord> result = new ArrayList<>();
        for (TransactionRecord record : all) {
            boolean afterFrom = from == null || !record.postDate().isBefore(from);
            boolean beforeTo = to == null || !record.postDate().isAfter(to);
            if (afterFrom && beforeTo) {
                result.add(record);
            }
        }
        result.sort(Comparator.comparing(TransactionRecord::txnTime).reversed());
        return result;
    }
}
