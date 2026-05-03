package com.example.agentserver.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * 坐席服务节点配置属性
 */
@Component
@ConfigurationProperties(prefix = "agentserver")
public class AgentServerProperties {
    private String serviceId;
    private String serviceName = "backend-service";
    private String authToken = "star-connection-token";

    // 获取器和设置器
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

    public String getAuthToken() {
        return authToken;
    }

    public void setAuthToken(String authToken) {
        this.authToken = authToken;
    }
}