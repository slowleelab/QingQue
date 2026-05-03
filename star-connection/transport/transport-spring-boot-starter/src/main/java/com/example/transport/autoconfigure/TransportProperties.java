package com.example.transport.autoconfigure;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Transport 配置属性
 */
@ConfigurationProperties(prefix = "transport")
public class TransportProperties {

    private ServerProperties server = new ServerProperties();
    private ClientProperties client = new ClientProperties();
    private HeartbeatProperties heartbeat = new HeartbeatProperties();
    private ReconnectionProperties reconnection = new ReconnectionProperties();
    private PoolProperties pool = new PoolProperties();

    public ServerProperties getServer() {
        return server;
    }

    public void setServer(ServerProperties server) {
        this.server = server;
    }

    public ClientProperties getClient() {
        return client;
    }

    public void setClient(ClientProperties client) {
        this.client = client;
    }

    public HeartbeatProperties getHeartbeat() {
        return heartbeat;
    }

    public void setHeartbeat(HeartbeatProperties heartbeat) {
        this.heartbeat = heartbeat;
    }

    public ReconnectionProperties getReconnection() {
        return reconnection;
    }

    public void setReconnection(ReconnectionProperties reconnection) {
        this.reconnection = reconnection;
    }

    public PoolProperties getPool() {
        return pool;
    }

    public void setPool(PoolProperties pool) {
        this.pool = pool;
    }

    /**
     * 服务端配置
     */
    public static class ServerProperties {
        private boolean enabled = false;
        private int port = 8888;
        private int bossThreads = 1;
        private int workerThreads = 8;
        private int soBacklog = 128;
        private boolean keepAlive = true;
        private int readIdleSeconds = 60;

        public boolean isEnabled() {
            return enabled;
        }

        public void setEnabled(boolean enabled) {
            this.enabled = enabled;
        }

        public int getPort() {
            return port;
        }

        public void setPort(int port) {
            this.port = port;
        }

        public int getBossThreads() {
            return bossThreads;
        }

        public void setBossThreads(int bossThreads) {
            this.bossThreads = bossThreads;
        }

        public int getWorkerThreads() {
            return workerThreads;
        }

        public void setWorkerThreads(int workerThreads) {
            this.workerThreads = workerThreads;
        }

        public int getSoBacklog() {
            return soBacklog;
        }

        public void setSoBacklog(int soBacklog) {
            this.soBacklog = soBacklog;
        }

        public boolean isKeepAlive() {
            return keepAlive;
        }

        public void setKeepAlive(boolean keepAlive) {
            this.keepAlive = keepAlive;
        }

        public int getReadIdleSeconds() {
            return readIdleSeconds;
        }

        public void setReadIdleSeconds(int readIdleSeconds) {
            this.readIdleSeconds = readIdleSeconds;
        }
    }

    /**
     * 客户端配置
     */
    public static class ClientProperties {
        private boolean enabled = false;
        private String defaultHost = "localhost";
        private int defaultPort = 8888;
        private int connectTimeoutMs = 10000;
        private int writeIdleSeconds = 20;
        private boolean multiRouterEnabled = true;

        public boolean isEnabled() {
            return enabled;
        }

        public void setEnabled(boolean enabled) {
            this.enabled = enabled;
        }

        public String getDefaultHost() {
            return defaultHost;
        }

        public void setDefaultHost(String defaultHost) {
            this.defaultHost = defaultHost;
        }

        public int getDefaultPort() {
            return defaultPort;
        }

        public void setDefaultPort(int defaultPort) {
            this.defaultPort = defaultPort;
        }

        public int getConnectTimeoutMs() {
            return connectTimeoutMs;
        }

        public void setConnectTimeoutMs(int connectTimeoutMs) {
            this.connectTimeoutMs = connectTimeoutMs;
        }

        public int getWriteIdleSeconds() {
            return writeIdleSeconds;
        }

        public void setWriteIdleSeconds(int writeIdleSeconds) {
            this.writeIdleSeconds = writeIdleSeconds;
        }

        public boolean isMultiRouterEnabled() {
            return multiRouterEnabled;
        }

        public void setMultiRouterEnabled(boolean multiRouterEnabled) {
            this.multiRouterEnabled = multiRouterEnabled;
        }
    }

    /**
     * 心跳配置
     */
    public static class HeartbeatProperties {
        private boolean enabled = true;
        private int intervalSeconds = 20;
        private int timeoutSeconds = 60;
        private int maxMissed = 3;

        public boolean isEnabled() {
            return enabled;
        }

        public void setEnabled(boolean enabled) {
            this.enabled = enabled;
        }

        public int getIntervalSeconds() {
            return intervalSeconds;
        }

        public void setIntervalSeconds(int intervalSeconds) {
            this.intervalSeconds = intervalSeconds;
        }

        public int getTimeoutSeconds() {
            return timeoutSeconds;
        }

        public void setTimeoutSeconds(int timeoutSeconds) {
            this.timeoutSeconds = timeoutSeconds;
        }

        public int getMaxMissed() {
            return maxMissed;
        }

        public void setMaxMissed(int maxMissed) {
            this.maxMissed = maxMissed;
        }
    }

    /**
     * 重连配置
     */
    public static class ReconnectionProperties {
        private boolean enabled = true;
        private int initialDelayMs = 1000;
        private int maxDelayMs = 300000;
        private int maxRetries = 10;
        private double jitterFactor = 0.25;

        public boolean isEnabled() {
            return enabled;
        }

        public void setEnabled(boolean enabled) {
            this.enabled = enabled;
        }

        public int getInitialDelayMs() {
            return initialDelayMs;
        }

        public void setInitialDelayMs(int initialDelayMs) {
            this.initialDelayMs = initialDelayMs;
        }

        public int getMaxDelayMs() {
            return maxDelayMs;
        }

        public void setMaxDelayMs(int maxDelayMs) {
            this.maxDelayMs = maxDelayMs;
        }

        public int getMaxRetries() {
            return maxRetries;
        }

        public void setMaxRetries(int maxRetries) {
            this.maxRetries = maxRetries;
        }

        public double getJitterFactor() {
            return jitterFactor;
        }

        public void setJitterFactor(double jitterFactor) {
            this.jitterFactor = jitterFactor;
        }
    }

    /**
     * 连接池配置
     */
    public static class PoolProperties {
        private int maxConnectionsPerTarget = 3;
        private int minIdleConnections = 1;
        private long maxIdleTimeMs = 300000;
        private long acquisitionTimeoutMs = 5000;

        public int getMaxConnectionsPerTarget() {
            return maxConnectionsPerTarget;
        }

        public void setMaxConnectionsPerTarget(int maxConnectionsPerTarget) {
            this.maxConnectionsPerTarget = maxConnectionsPerTarget;
        }

        public int getMinIdleConnections() {
            return minIdleConnections;
        }

        public void setMinIdleConnections(int minIdleConnections) {
            this.minIdleConnections = minIdleConnections;
        }

        public long getMaxIdleTimeMs() {
            return maxIdleTimeMs;
        }

        public void setMaxIdleTimeMs(long maxIdleTimeMs) {
            this.maxIdleTimeMs = maxIdleTimeMs;
        }

        public long getAcquisitionTimeoutMs() {
            return acquisitionTimeoutMs;
        }

        public void setAcquisitionTimeoutMs(long acquisitionTimeoutMs) {
            this.acquisitionTimeoutMs = acquisitionTimeoutMs;
        }
    }
}
