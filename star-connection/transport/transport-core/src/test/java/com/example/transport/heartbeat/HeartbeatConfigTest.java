package com.example.transport.heartbeat;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class HeartbeatConfigTest {

    @Test
    void testDefaultValues() {
        HeartbeatConfig config = new HeartbeatConfig();

        assertEquals(20, config.getIntervalSeconds());
        assertEquals(60, config.getTimeoutSeconds());
        assertEquals(3, config.getMaxMissedHeartbeats());
        assertTrue(config.isEnabled());
    }

    @Test
    void testCustomValues() {
        HeartbeatConfig config = new HeartbeatConfig(30, 120);

        assertEquals(30, config.getIntervalSeconds());
        assertEquals(120, config.getTimeoutSeconds());
    }

    @Test
    void testSetters() {
        HeartbeatConfig config = new HeartbeatConfig();

        config.setIntervalSeconds(15);
        config.setTimeoutSeconds(45);
        config.setMaxMissedHeartbeats(5);
        config.setEnabled(false);

        assertEquals(15, config.getIntervalSeconds());
        assertEquals(45, config.getTimeoutSeconds());
        assertEquals(5, config.getMaxMissedHeartbeats());
        assertFalse(config.isEnabled());
    }
}
