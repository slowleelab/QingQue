package com.example.customerserver.dto;

/**
 * 会话响应
 */
public class SessionResponse {
    private String sessionId;
    private String customerId;
    private String customerName;
    private String agentId;
    private String agentName;
    private String status;
    private long createTime;
    private String message;

    public SessionResponse() {
    }

    public static SessionResponse success(String sessionId, String customerId, String status) {
        SessionResponse response = new SessionResponse();
        response.setSessionId(sessionId);
        response.setCustomerId(customerId);
        response.setStatus(status);
        return response;
    }

    public static SessionResponse waiting(String sessionId, String customerId) {
        SessionResponse response = new SessionResponse();
        response.setSessionId(sessionId);
        response.setCustomerId(customerId);
        response.setStatus("WAITING");
        response.setMessage("等待坐席分配");
        return response;
    }

    public static SessionResponse error(String message) {
        SessionResponse response = new SessionResponse();
        response.setStatus("ERROR");
        response.setMessage(message);
        return response;
    }

    public String getSessionId() {
        return sessionId;
    }

    public void setSessionId(String sessionId) {
        this.sessionId = sessionId;
    }

    public String getCustomerId() {
        return customerId;
    }

    public void setCustomerId(String customerId) {
        this.customerId = customerId;
    }

    public String getCustomerName() {
        return customerName;
    }

    public void setCustomerName(String customerName) {
        this.customerName = customerName;
    }

    public String getAgentId() {
        return agentId;
    }

    public void setAgentId(String agentId) {
        this.agentId = agentId;
    }

    public String getAgentName() {
        return agentName;
    }

    public void setAgentName(String agentName) {
        this.agentName = agentName;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public long getCreateTime() {
        return createTime;
    }

    public void setCreateTime(long createTime) {
        this.createTime = createTime;
    }

    public String getMessage() {
        return message;
    }

    public void setMessage(String message) {
        this.message = message;
    }
}
