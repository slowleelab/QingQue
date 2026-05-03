package com.example.transport.connection;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class ConnectionListenerTest {

    @Test
    void testOnConnected() {
        ConnectionListener listener = new ConnectionListener() {
            @Override
            public void onConnected(Connection connection) {
                assertNotNull(connection);
            }
        };
        // Test that the interface has the default method
        listener.onDisconnected(null); // Should not throw
        listener.onError(null, null); // Should not throw
    }

    @Test
    void testOnDisconnected() {
        ConnectionListener listener = new ConnectionListener() {
            @Override
            public void onDisconnected(Connection connection) {
                // Custom implementation - null is allowed as default method parameter
            }
        };
        assertDoesNotThrow(() -> listener.onDisconnected(null));
    }

    @Test
    void testOnError() {
        ConnectionListener listener = new ConnectionListener() {
            @Override
            public void onError(Connection connection, Throwable error) {
                assertNotNull(error);
            }
        };
        listener.onError(null, new RuntimeException("test")); // Should not throw
    }

    @Test
    void testDefaultMethods() {
        ConnectionListener listener = new ConnectionListener() {};

        // All default methods should work without throwing
        assertDoesNotThrow(() -> listener.onConnected(null));
        assertDoesNotThrow(() -> listener.onDisconnected(null));
        assertDoesNotThrow(() -> listener.onError(null, null));
    }
}
