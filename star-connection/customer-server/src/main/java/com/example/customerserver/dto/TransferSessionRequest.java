package com.example.customerserver.dto;

import java.util.List;
import java.util.Map;

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
    public void setSessionId(String s) { this.sessionId = s; }
    public String getCustomerId() { return customerId; }
    public void setCustomerId(String s) { this.customerId = s; }
    public String getTransferReason() { return transferReason; }
    public void setTransferReason(String s) { this.transferReason = s; }
    public String getTransferSummary() { return transferSummary; }
    public void setTransferSummary(String s) { this.transferSummary = s; }
    public List<Map<String, String>> getHistory() { return history; }
    public void setHistory(List<Map<String, String>> h) { this.history = h; }
    public String getIntent() { return intent; }
    public void setIntent(String s) { this.intent = s; }
    public String getSentiment() { return sentiment; }
    public void setSentiment(String s) { this.sentiment = s; }
    public String getVipLevel() { return vipLevel; }
    public void setVipLevel(String s) { this.vipLevel = s; }
}
