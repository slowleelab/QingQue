package com.example.customerserver.dto;

import java.util.List;
import java.util.Map;

/**
 * 转接会话请求 — SmartCS Bot 转接到人工坐席时发送
 */
public class TransferSessionRequest {
    private String sessionId;
    private String customerId;
    private String transferReason;
    private String transferSummary;
    private List<Map<String, String>> history;
    private String intent;
    private String sentiment;
    private String vipLevel;

    public String getSessionId() { return sessionId; }
    public void setSessionId(String sessionId) { this.sessionId = sessionId; }
    public String getCustomerId() { return customerId; }
    public void setCustomerId(String customerId) { this.customerId = customerId; }
    public String getTransferReason() { return transferReason; }
    public void setTransferReason(String transferReason) { this.transferReason = transferReason; }
    public String getTransferSummary() { return transferSummary; }
    public void setTransferSummary(String transferSummary) { this.transferSummary = transferSummary; }
    public List<Map<String, String>> getHistory() { return history; }
    public void setHistory(List<Map<String, String>> history) { this.history = history; }
    public String getIntent() { return intent; }
    public void setIntent(String intent) { this.intent = intent; }
    public String getSentiment() { return sentiment; }
    public void setSentiment(String sentiment) { this.sentiment = sentiment; }
    public String getVipLevel() { return vipLevel; }
    public void setVipLevel(String vipLevel) { this.vipLevel = vipLevel; }
}
