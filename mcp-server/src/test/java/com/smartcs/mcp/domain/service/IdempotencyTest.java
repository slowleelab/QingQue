package com.smartcs.mcp.domain.service;

import static org.assertj.core.api.Assertions.assertThat;

import com.smartcs.mcp.adapter.mock.DemoCards;
import com.smartcs.mcp.adapter.mock.InMemoryCardAccountRepository;
import com.smartcs.mcp.adapter.mock.InMemoryIdempotencyStore;
import com.smartcs.mcp.adapter.mock.MockDataSeeder;
import com.smartcs.mcp.config.CreditCardProperties;
import com.smartcs.mcp.model.RepaymentResult;
import java.math.BigDecimal;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * 幂等性测试：同一幂等键的重复受理只生效一次并回放原结果；不同键或无键则各自执行。
 */
class IdempotencyTest {

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
    void sameKeyReplaysOriginalResultAndAppliesOnce() {
        RepaymentResult first = paymentService.repay(CARD, new BigDecimal("1000"), null, "req-1");
        RepaymentResult second = paymentService.repay(CARD, new BigDecimal("1000"), null, "req-1");

        // 回放原结果：流水号一致
        assertThat(second.referenceNo()).isEqualTo(first.referenceNo());
        // 仅受理一次：已还金额只增加 1000
        assertThat(repository.findByCardNo(CARD).getRepaidAmount()).isEqualByComparingTo("1000");
    }

    @Test
    void differentKeysApplyIndependently() {
        paymentService.repay(CARD, new BigDecimal("1000"), null, "req-1");
        paymentService.repay(CARD, new BigDecimal("1000"), null, "req-2");
        assertThat(repository.findByCardNo(CARD).getRepaidAmount()).isEqualByComparingTo("2000");
    }

    @Test
    void noKeyAlwaysApplies() {
        paymentService.repay(CARD, new BigDecimal("500"), null, null);
        paymentService.repay(CARD, new BigDecimal("500"), null, "");
        assertThat(repository.findByCardNo(CARD).getRepaidAmount()).isEqualByComparingTo("1000");
    }
}
