package com.example.customerserver.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * 客户服务节点配置属性
 */
@Component
@ConfigurationProperties(prefix = "customerserver")
public class CustomerServerProperties {
    private String serviceId = "frontend-1";
    private String serviceName = "customer-frontend-service";
    private boolean registerToZookeeper = true;
    private Cluster cluster = new Cluster();

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

    public boolean isRegisterToZookeeper() {
        return registerToZookeeper;
    }

    public void setRegisterToZookeeper(boolean registerToZookeeper) {
        this.registerToZookeeper = registerToZookeeper;
    }

    public Cluster getCluster() {
        return cluster;
    }

    public void setCluster(Cluster cluster) {
        this.cluster = cluster;
    }

    /**
     * 集群配置
     */
    public static class Cluster {
        private boolean enabled = false;

        public boolean isEnabled() {
            return enabled;
        }

        public void setEnabled(boolean enabled) {
            this.enabled = enabled;
        }
    }
}
