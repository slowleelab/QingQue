package com.example.transport.heartbeat;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class HeartbeatManagerTest {

    @Test
    void testHeartbeatListenerDefaultMethods() {
        HeartbeatManager.HeartbeatListener listener = new HeartbeatManager.HeartbeatListener() {};

        // All default methods should work without throwing
        assertDoesNotThrow(() -> listener.onHeartbeatSent("conn-1"));
        assertDoesNotThrow(() -> listener.onHeartbeatReceived("conn-1"));
        assertDoesNotThrow(() -> listener.onHeartbeatTimeout("conn-1"));
    }

    @Test
    void testHeartbeatListenerCustomImplementation() {
        StringBuilder sb = new StringBuilder();
        HeartbeatManager.HeartbeatListener listener = new HeartbeatManager.HeartbeatListener() {
            @Override
            public void onHeartbeatSent(String connectionId) {
                sb.append("sent:").append(connectionId);
            }

            @Override
            public void onHeartbeatReceived(String connectionId) {
                sb.append("received:").append(connectionId);
            }

            @Override
            public void onHeartbeatTimeout(String connectionId) {
                sb.append("timeout:").append(connectionId);
            }
        };

        listener.onHeartbeatSent("conn-1");
        listener.onHeartbeatReceived("conn-1");
        listener.onHeartbeatTimeout("conn-1");

        assertEquals("sent:conn-1received:conn-1timeout:conn-1", sb.toString());
    }
}
