package com.smartcs.mcp.domain.service;

import static org.assertj.core.api.Assertions.assertThat;

import com.smartcs.mcp.adapter.mock.DemoCards;
import com.smartcs.mcp.adapter.mock.InMemoryCardAccountRepository;
import com.smartcs.mcp.adapter.mock.InMemoryIdempotencyStore;
import com.smartcs.mcp.adapter.mock.MockDataSeeder;
import com.smartcs.mcp.config.CreditCardProperties;
import java.math.BigDecimal;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * 并发一致性测试：多线程并发还款后，账户终态与串行等价（每账户 ReentrantLock 消除竞态）。
 */
class ConcurrencyTest {

    private static final String CARD = DemoCards.PRIMARY;

    private InMemoryCardAccountRepository repository;
    private PaymentService paymentService;

    @BeforeEach
    void setUp() {
        repository = new InMemoryCardAccountRepository();
        new MockDataSeeder(repository).seed();
        CreditCardProperties props = new CreditCardProperties();
        IdempotentExecutor idempotent = new IdempotentExecutor(new InMemoryIdempotencyStore(props));
        paymentService = new PaymentService(repository, idempotent, props);
    }

    @Test
    void concurrentRepaymentsReachConsistentFinalState() throws InterruptedException {
        int threads = 32;
        BigDecimal each = new BigDecimal("100");
        ExecutorService pool = Executors.newFixedThreadPool(8);
        CountDownLatch start = new CountDownLatch(1);
        CountDownLatch done = new CountDownLatch(threads);
        AtomicInteger failures = new AtomicInteger();

        for (int i = 0; i < threads; i++) {
            pool.submit(() -> {
                try {
                    start.await();
                    paymentService.repay(CARD, each, null, null);
                } catch (Exception e) {
                    failures.incrementAndGet();
                } finally {
                    done.countDown();
                }
            });
        }
        start.countDown();
        assertThat(done.await(10, TimeUnit.SECONDS)).isTrue();
        pool.shutdownNow();

        assertThat(failures.get()).isZero();
        // 初始已还 0，32 次各还 100 → 终态 3200
        assertThat(repository.findByCardNo(CARD).getRepaidAmount()).isEqualByComparingTo("3200");
        // 初始已用 18650，冲减 3200 → 15450
        assertThat(repository.findByCardNo(CARD).getUsedAmount()).isEqualByComparingTo("15450");
    }
}
