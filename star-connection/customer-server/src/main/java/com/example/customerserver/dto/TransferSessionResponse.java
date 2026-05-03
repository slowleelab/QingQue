package com.example.customerserver.dto;

/**
 * 转接会话响应 — 返回给 SmartCS Bot 的会话创建结果
 */
public class TransferSessionResponse {
    private String sessionId;
    private String pollUrl;
    private String sendUrl;
    private String token;
    private String status;

    public TransferSessionResponse(String sessionId, String pollUrl, String sendUrl, String token) {
        this.sessionId = sessionId;
        this.pollUrl = pollUrl;
        this.sendUrl = sendUrl;
        this.token = token;
        this.status = "WAITING";
    }

    public String getSessionId() { return sessionId; }
    public String getPollUrl() { return pollUrl; }
    public String getSendUrl() { return sendUrl; }
    public String getToken() { return token; }
    public String getStatus() { return status; }
}
