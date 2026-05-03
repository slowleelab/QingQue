package com.example.transport.connection;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class ConnectionPoolListenerTest {

    @Test
    void testDefaultMethods() {
        ConnectionPool.ConnectionPoolListener listener = new ConnectionPool.ConnectionPoolListener() {};

        // All default methods should work without throwing
        assertDoesNotThrow(() -> listener.onConnectionCreated("target-1", null));
        assertDoesNotThrow(() -> listener.onConnectionClosed("target-1", null));
        assertDoesNotThrow(() -> listener.onConnectionLost("target-1", null));
    }

    @Test
    void testCustomImplementation() {
        StringBuilder sb = new StringBuilder();
        ConnectionPool.ConnectionPoolListener listener = new ConnectionPool.ConnectionPoolListener() {
            @Override
            public void onConnectionCreated(String targetId, Connection connection) {
                sb.append("created:").append(targetId);
            }

            @Override
            public void onConnectionClosed(String targetId, Connection connection) {
                sb.append("closed:").append(targetId);
            }

            @Override
            public void onConnectionLost(String targetId, Connection connection) {
                sb.append("lost:").append(targetId);
            }
        };

        listener.onConnectionCreated("target-1", null);
        listener.onConnectionClosed("target-1", null);
        listener.onConnectionLost("target-2", null);

        assertEquals("created:target-1closed:target-1lost:target-2", sb.toString());
    }
}
