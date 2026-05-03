package com.example.common.model;

import java.io.Serializable;

/**
 * 聊天消息实体
 */
public class ChatMessage implements Serializable {
    private static final long serialVersionUID = 1L;

    /**
     * 消息ID
     */
    private String messageId;

    /**
     * 会话ID
     */
    private String sessionId;

    /**
     * 发送者类型
     */
    private SenderType senderType;

    /**
     * 发送者ID
     */
    private String senderId;

    /**
     * 发送者名称
     */
    private String senderName;

    /**
     * 消息内容
     */
    private String content;

    /**
     * 消息类型（text, image, file等）
     */
    private String contentType;

    /**
     * 时间戳（毫秒）
     */
    private long timestamp;

    public ChatMessage() {
        this.timestamp = System.currentTimeMillis();
        this.contentType = "text";
    }

    public ChatMessage(String sessionId, SenderType senderType, String senderId, String content) {
        this();
        this.sessionId = sessionId;
        this.senderType = senderType;
        this.senderId = senderId;
        this.content = content;
    }

    // Getters and Setters
    public String getMessageId() {
        return messageId;
    }

    public void setMessageId(String messageId) {
        this.messageId = messageId;
    }

    public String getSessionId() {
        return sessionId;
    }

    public void setSessionId(String sessionId) {
        this.sessionId = sessionId;
    }

    public SenderType getSenderType() {
        return senderType;
    }

    public void setSenderType(SenderType senderType) {
        this.senderType = senderType;
    }

    public String getSenderId() {
        return senderId;
    }

    public void setSenderId(String senderId) {
        this.senderId = senderId;
    }

    public String getSenderName() {
        return senderName;
    }

    public void setSenderName(String senderName) {
        this.senderName = senderName;
    }

    public String getContent() {
        return content;
    }

    public void setContent(String content) {
        this.content = content;
    }

    public String getContentType() {
        return contentType;
    }

    public void setContentType(String contentType) {
        this.contentType = contentType;
    }

    public long getTimestamp() {
        return timestamp;
    }

    public void setTimestamp(long timestamp) {
        this.timestamp = timestamp;
    }

    /**
     * 创建客户消息
     */
    public static ChatMessage fromCustomer(String sessionId, String customerId, String customerName, String content) {
        ChatMessage message = new ChatMessage(sessionId, SenderType.CUSTOMER, customerId, content);
        message.setSenderName(customerName);
        return message;
    }

    /**
     * 创建坐席消息
     */
    public static ChatMessage fromAgent(String sessionId, String agentId, String agentName, String content) {
        ChatMessage message = new ChatMessage(sessionId, SenderType.AGENT, agentId, content);
        message.setSenderName(agentName);
        return message;
    }

    /**
     * 创建系统消息
     */
    public static ChatMessage systemMessage(String sessionId, String content) {
        ChatMessage message = new ChatMessage(sessionId, SenderType.SYSTEM, "system", content);
        message.setSenderName("系统");
        return message;
    }

    @Override
    public String toString() {
        return "ChatMessage{" +
                "messageId='" + messageId + '\'' +
                ", sessionId='" + sessionId + '\'' +
                ", senderType=" + senderType +
                ", senderId='" + senderId + '\'' +
                ", senderName='" + senderName + '\'' +
                ", content='" + (content != null && content.length() > 50 ? content.substring(0, 50) + "..." : content) + '\'' +
                ", contentType='" + contentType + '\'' +
                ", timestamp=" + timestamp +
                '}';
    }
}
