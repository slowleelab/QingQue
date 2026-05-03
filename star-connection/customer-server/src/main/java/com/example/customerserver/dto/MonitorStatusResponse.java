package com.example.customerserver.dto;

/**
 * 综合监控状态响应
 */
public class MonitorStatusResponse {
    private ServerStatus server;
    private ConnectionStatistics connections;
    private long timestamp;

    public MonitorStatusResponse() {
        this.timestamp = System.currentTimeMillis();
    }

    public ServerStatus getServer() {
        return server;
    }

    public void setServer(ServerStatus server) {
        this.server = server;
    }

    public ConnectionStatistics getConnections() {
        return connections;
    }

    public void setConnections(ConnectionStatistics connections) {
        this.connections = connections;
    }

    public long getTimestamp() {
        return timestamp;
    }

    public void setTimestamp(long timestamp) {
        this.timestamp = timestamp;
    }

    /**
     * 连接统计信息
     */
    public static class ConnectionStatistics {
        private int activeCount;
        private int authenticatedCount;
        private int zkServiceCount;

        public int getActiveCount() {
            return activeCount;
        }

        public void setActiveCount(int activeCount) {
            this.activeCount = activeCount;
        }

        public int getAuthenticatedCount() {
            return authenticatedCount;
        }

        public void setAuthenticatedCount(int authenticatedCount) {
            this.authenticatedCount = authenticatedCount;
        }

        public int getZkServiceCount() {
            return zkServiceCount;
        }

        public void setZkServiceCount(int zkServiceCount) {
            this.zkServiceCount = zkServiceCount;
        }
    }
}
