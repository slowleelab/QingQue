package com.example.transport.reconnection;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class ExponentialBackoffPolicyTest {

    private ExponentialBackoffPolicy policy;

    @BeforeEach
    void setUp() {
        policy = new ExponentialBackoffPolicy(1000, 30000, 5, 2.0, 0.0);
    }

    @Test
    void testInitialDelay() {
        long delay = policy.computeDelay(1);
        assertEquals(1000, delay);
    }

    @Test
    void testExponentialBackoff() {
        assertEquals(1000, policy.computeDelay(1));
        assertEquals(2000, policy.computeDelay(2));
        assertEquals(4000, policy.computeDelay(3));
        assertEquals(8000, policy.computeDelay(4));
    }

    @Test
    void testMaxDelay() {
        ExponentialBackoffPolicy shortMaxPolicy = new ExponentialBackoffPolicy(1000, 5000, 10, 2.0, 0.0);

        long delay = shortMaxPolicy.computeDelay(10);

        assertTrue(delay <= 5000);
    }

    @Test
    void testShouldRetryWithinLimit() {
        assertTrue(policy.shouldRetry(1));
        assertTrue(policy.shouldRetry(3));
        assertTrue(policy.shouldRetry(5));
    }

    @Test
    void testShouldNotRetryExceedLimit() {
        assertFalse(policy.shouldRetry(6));
        assertFalse(policy.shouldRetry(10));
    }

    @Test
    void testReset() {
        policy.incrementAttempt();
        policy.incrementAttempt();
        assertEquals(2, policy.getCurrentAttempt());

        policy.reset();

        assertEquals(0, policy.getCurrentAttempt());
    }

    @Test
    void testBuilder() {
        ExponentialBackoffPolicy customPolicy = ExponentialBackoffPolicy.builder()
                .initialDelayMs(500)
                .maxDelayMs(10000)
                .maxRetries(3)
                .multiplier(1.5)
                .jitterFactor(0.0)  // No jitter for predictable test
                .build();

        assertEquals(500, customPolicy.computeDelay(1));
        assertEquals(3, customPolicy.getMaxRetries());
    }

    @Test
    void testJitterFactor() {
        ExponentialBackoffPolicy jitterPolicy = new ExponentialBackoffPolicy(1000, 10000, 5, 2.0, 0.5);

        // With jitter, delays should vary but stay within bounds
        for (int i = 0; i < 10; i++) {
            long delay = jitterPolicy.computeDelay(1);
            assertTrue(delay >= 500 && delay <= 1500, "Delay should be within jitter bounds");
        }
    }

    @Test
    void testIncrementAttempt() {
        assertEquals(1, policy.incrementAttempt());
        assertEquals(2, policy.incrementAttempt());
        assertEquals(3, policy.incrementAttempt());
    }
}
