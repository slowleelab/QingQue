package com.example.customerserver.dto;

/**
 * 连接状态详情
 */
public class ConnectionStatus {
    private String serviceId;
    private String channelId;
    private String status;
    private String remoteAddress;
    private boolean authenticated;
    private long connectedSince;

    public ConnectionStatus() {
    }

    public ConnectionStatus(String serviceId, String channelId, String status, String remoteAddress, boolean authenticated) {
        this.serviceId = serviceId;
        this.channelId = channelId;
        this.status = status;
        this.remoteAddress = remoteAddress;
        this.authenticated = authenticated;
    }

    public String getServiceId() {
        return serviceId;
    }

    public void setServiceId(String serviceId) {
        this.serviceId = serviceId;
    }

    public String getChannelId() {
        return channelId;
    }

    public void setChannelId(String channelId) {
        this.channelId = channelId;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public String getRemoteAddress() {
        return remoteAddress;
    }

    public void setRemoteAddress(String remoteAddress) {
        this.remoteAddress = remoteAddress;
    }

    public boolean isAuthenticated() {
        return authenticated;
    }

    public void setAuthenticated(boolean authenticated) {
        this.authenticated = authenticated;
    }

    public long getConnectedSince() {
        return connectedSince;
    }

    public void setConnectedSince(long connectedSince) {
        this.connectedSince = connectedSince;
    }
}
