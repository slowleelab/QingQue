package com.example.agentserver.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * 客户端连接配置
 */
@Component
@ConfigurationProperties(prefix = "client.connection")
public class ClientConnectionProperties {
    /**
     * 是否启用服务发现（从ZK发现路由节点）
     */
    private boolean discoveryEnabled = true;

    /**
     * 路由服务名称
     */
    private String routerServiceName = "router-service";

    /**
     * 连接重试间隔（毫秒）
     */
    private long reconnectIntervalMs = 5000;

    /**
     * 服务发现刷新间隔（毫秒）
     */
    private long discoveryRefreshIntervalMs = 10000;

    /**
     * 是否连接到所有路由节点（集群模式）
     */
    private boolean connectToAllRouters = true;

    public boolean isDiscoveryEnabled() {
        return discoveryEnabled;
    }

    public void setDiscoveryEnabled(boolean discoveryEnabled) {
        this.discoveryEnabled = discoveryEnabled;
    }

    public String getRouterServiceName() {
        return routerServiceName;
    }

    public void setRouterServiceName(String routerServiceName) {
        this.routerServiceName = routerServiceName;
    }

    public long getReconnectIntervalMs() {
        return reconnectIntervalMs;
    }

    public void setReconnectIntervalMs(long reconnectIntervalMs) {
        this.reconnectIntervalMs = reconnectIntervalMs;
    }

    public long getDiscoveryRefreshIntervalMs() {
        return discoveryRefreshIntervalMs;
    }

    public void setDiscoveryRefreshIntervalMs(long discoveryRefreshIntervalMs) {
        this.discoveryRefreshIntervalMs = discoveryRefreshIntervalMs;
    }

    public boolean isConnectToAllRouters() {
        return connectToAllRouters;
    }

    public void setConnectToAllRouters(boolean connectToAllRouters) {
        this.connectToAllRouters = connectToAllRouters;
    }
}
