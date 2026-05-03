package com.example.agentserver.dto;

import java.util.Map;

/**
 * 客户端状态响应
 */
public class ClientStatusResponse {
    private String serviceId;
    private String serviceName;
    private ConnectionStatus connection;
    private RegistrationStatus registration;
    private long timestamp;

    public ClientStatusResponse() {
        this.timestamp = System.currentTimeMillis();
    }

    public String getServiceId() {
        return serviceId;
    }

    public void setServiceId(String serviceId) {
        this.serviceId = serviceId;
    }

    public String getServiceName() {
        return serviceName;
    }

    public void setServiceName(String serviceName) {
        this.serviceName = serviceName;
    }

    public ConnectionStatus getConnection() {
        return connection;
    }

    public void setConnection(ConnectionStatus connection) {
        this.connection = connection;
    }

    public RegistrationStatus getRegistration() {
        return registration;
    }

    public void setRegistration(RegistrationStatus registration) {
        this.registration = registration;
    }

    public long getTimestamp() {
        return timestamp;
    }

    public void setTimestamp(long timestamp) {
        this.timestamp = timestamp;
    }

    /**
     * 连接状态
     */
    public static class ConnectionStatus {
        private boolean connected;
        private String serverHost;
        private int serverPort;
        private String status;

        public boolean isConnected() {
            return connected;
        }

        public void setConnected(boolean connected) {
            this.connected = connected;
        }

        public String getServerHost() {
            return serverHost;
        }

        public void setServerHost(String serverHost) {
            this.serverHost = serverHost;
        }

        public int getServerPort() {
            return serverPort;
        }

        public void setServerPort(int serverPort) {
            this.serverPort = serverPort;
        }

        public String getStatus() {
            return status;
        }

        public void setStatus(String status) {
            this.status = status;
        }
    }

    /**
     * 注册状态
     */
    public static class RegistrationStatus {
        private boolean registered;
        private String zookeeperConnected;
        private Map<String, String> metadata;

        public boolean isRegistered() {
            return registered;
        }

        public void setRegistered(boolean registered) {
            this.registered = registered;
        }

        public String getZookeeperConnected() {
            return zookeeperConnected;
        }

        public void setZookeeperConnected(String zookeeperConnected) {
            this.zookeeperConnected = zookeeperConnected;
        }

        public Map<String, String> getMetadata() {
            return metadata;
        }

        public void setMetadata(Map<String, String> metadata) {
            this.metadata = metadata;
        }
    }
}
