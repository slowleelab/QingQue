package com.example.transport.reconnection;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class ReconnectionPolicyTest {

    @Test
    void testExponentialBackoffPolicyImplementsReconnectionPolicy() {
        ReconnectionPolicy policy = new ExponentialBackoffPolicy(1000, 30000, 5, 2.0, 0.0);

        // Test interface methods
        assertEquals(5, policy.getMaxRetries());
        assertEquals(0, policy.getCurrentAttempt());

        // Test should retry
        assertTrue(policy.shouldRetry(1));
        assertTrue(policy.shouldRetry(5));
        assertFalse(policy.shouldRetry(6));

        // Test delay computation
        long delay = policy.computeDelay(1);
        assertTrue(delay >= 1000);
        assertTrue(delay <= 30000);

        // Test reset
        policy.reset();
        assertEquals(0, policy.getCurrentAttempt());
    }

    @Test
    void testPolicyInterfaceContract() {
        ReconnectionPolicy policy = new ExponentialBackoffPolicy(500, 10000, 3, 1.5, 0.0);

        // Test basic contract
        assertTrue(policy.getMaxRetries() > 0);
        assertTrue(policy.computeDelay(1) > 0);
        assertTrue(policy.computeDelay(2) >= policy.computeDelay(1)); // Increasing delay
    }
}
