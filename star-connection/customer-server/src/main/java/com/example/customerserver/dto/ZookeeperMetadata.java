package com.example.customerserver.dto;

import java.util.List;
import java.util.Map;

/**
 * ZooKeeper 元数据响应
 */
public class ZookeeperMetadata {
    private boolean connected;
    private String connectString;
    private List<ServiceMetadata> services;

    public ZookeeperMetadata() {
    }

    public boolean isConnected() {
        return connected;
    }

    public void setConnected(boolean connected) {
        this.connected = connected;
    }

    public String getConnectString() {
        return connectString;
    }

    public void setConnectString(String connectString) {
        this.connectString = connectString;
    }

    public List<ServiceMetadata> getServices() {
        return services;
    }

    public void setServices(List<ServiceMetadata> services) {
        this.services = services;
    }

    public static class ServiceMetadata {
        private String serviceId;
        private String serviceName;
        private String address;
        private int port;
        private Map<String, String> metadata;
        private String registrationTime;

        public ServiceMetadata() {
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

        public String getAddress() {
            return address;
        }

        public void setAddress(String address) {
            this.address = address;
        }

        public int getPort() {
            return port;
        }

        public void setPort(int port) {
            this.port = port;
        }

        public Map<String, String> getMetadata() {
            return metadata;
        }

        public void setMetadata(Map<String, String> metadata) {
            this.metadata = metadata;
        }

        public String getRegistrationTime() {
            return registrationTime;
        }

        public void setRegistrationTime(String registrationTime) {
            this.registrationTime = registrationTime;
        }
    }
}
