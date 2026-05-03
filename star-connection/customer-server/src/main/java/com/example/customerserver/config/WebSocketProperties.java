package com.example.customerserver.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * WebSocket 配置属性
 */
@Component
@ConfigurationProperties(prefix = "websocket")
public class WebSocketProperties {

    private Customer customer = new Customer();

    public Customer getCustomer() {
        return customer;
    }

    public void setCustomer(Customer customer) {
        this.customer = customer;
    }

    public static class Customer {
        private boolean enabled = false;
        private String path = "/ws/customer";
        private int port = 8889;
        private int maxWaitTimeSeconds = 300;
        private int heartbeatIntervalSeconds = 30;

        public boolean isEnabled() {
            return enabled;
        }

        public void setEnabled(boolean enabled) {
            this.enabled = enabled;
        }

        public String getPath() {
            return path;
        }

        public void setPath(String path) {
            this.path = path;
        }

        public int getPort() {
            return port;
        }

        public void setPort(int port) {
            this.port = port;
        }

        public int getMaxWaitTimeSeconds() {
            return maxWaitTimeSeconds;
        }

        public void setMaxWaitTimeSeconds(int maxWaitTimeSeconds) {
            this.maxWaitTimeSeconds = maxWaitTimeSeconds;
        }

        public int getHeartbeatIntervalSeconds() {
            return heartbeatIntervalSeconds;
        }

        public void setHeartbeatIntervalSeconds(int heartbeatIntervalSeconds) {
            this.heartbeatIntervalSeconds = heartbeatIntervalSeconds;
        }
    }
}
