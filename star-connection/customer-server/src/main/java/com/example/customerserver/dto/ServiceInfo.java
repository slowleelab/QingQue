package com.example.customerserver.dto;

/**
 * 服务信息
 */
public class ServiceInfo {
    private String id;
    private String name;
    private String address;
    private int port;
    private java.util.Map<String, String> metadata;

    public ServiceInfo() {
    }

    public ServiceInfo(String id, String name, String address, int port) {
        this.id = id;
        this.name = name;
        this.address = address;
        this.port = port;
    }

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
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

    public java.util.Map<String, String> getMetadata() {
        return metadata;
    }

    public void setMetadata(java.util.Map<String, String> metadata) {
        this.metadata = metadata;
    }
}
