package com.lumio.mcp.domain.port;

import com.lumio.mcp.domain.CardAccount;
import com.lumio.mcp.domain.TransactionRecord;
import java.time.LocalDate;
import java.util.List;
import java.util.function.Function;

/**
 * 信用卡账户仓储端口（六边形架构 driven port）。
 *
 * <p>领域服务只依赖本接口；具体数据来源由适配器实现。当前提供内存 Mock 适配器
 * （{@code adapter.mock.InMemoryCardAccountRepository}），未来可替换为对接真实核心系统的适配器，
 * 而无需改动领域服务与工具层。</p>
 */
public interface CardAccountRepository {

    /**
     * 按完整卡号读取账户快照。
     *
     * @throws com.lumio.mcp.domain.exception.BusinessException 账户不存在（ACCOUNT_NOT_FOUND）
     */
    CardAccount findByCardNo(String cardNo);

    /**
     * 在账户级临界区内原子地读取并变更账户状态，消除并发写竞态。
     *
     * @param cardNo   完整卡号
     * @param mutation 在持锁前提下执行的变更/计算函数，入参为账户对象，返回业务结果
     * @param <R>      结果类型
     * @return 变更函数的返回值
     * @throws com.lumio.mcp.domain.exception.BusinessException 账户不存在（ACCOUNT_NOT_FOUND）
     */
    <R> R updateAtomically(String cardNo, Function<CardAccount, R> mutation);

    /**
     * 查询指定日期区间的交易记录（按交易时间倒序）。
     *
     * @param from 起始日期（含）；为 {@code null} 表示不设下界
     * @param to   截止日期（含）；为 {@code null} 表示不设上界
     * @throws com.lumio.mcp.domain.exception.BusinessException 账户不存在（ACCOUNT_NOT_FOUND）
     */
    List<TransactionRecord> transactionsBetween(String cardNo, LocalDate from, LocalDate to);
}
