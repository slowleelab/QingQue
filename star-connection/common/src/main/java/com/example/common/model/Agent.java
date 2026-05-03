package com.example.common.model;

import java.io.Serializable;

/**
 * 坐席实体
 */
public class Agent implements Serializable {
    private static final long serialVersionUID = 1L;

    /**
     * 坐席ID
     */
    private String agentId;

    /**
     * 坐席名称
     */
    private String agentName;

    /**
     * 坐席状态
     */
    private AgentStatus status;

    /**
     * 最大并发会话数
     */
    private int maxSessions;

    /**
     * 当前会话数
     */
    private int currentSessions;

    /**
     * 所属后台节点ID
     */
    private String backendId;

    /**
     * 上线时间（毫秒）
     */
    private long onlineTime;

    public Agent() {
        this.status = AgentStatus.OFFLINE;
        this.maxSessions = 10;
        this.currentSessions = 0;
    }

    public Agent(String agentId) {
        this();
        this.agentId = agentId;
    }

    public Agent(String agentId, String agentName) {
        this(agentId);
        this.agentName = agentName;
    }

    // Getters and Setters
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

    public AgentStatus getStatus() {
        return status;
    }

    public void setStatus(AgentStatus status) {
        this.status = status;
    }

    public int getMaxSessions() {
        return maxSessions;
    }

    public void setMaxSessions(int maxSessions) {
        this.maxSessions = maxSessions;
    }

    public int getCurrentSessions() {
        return currentSessions;
    }

    public void setCurrentSessions(int currentSessions) {
        this.currentSessions = currentSessions;
    }

    public String getBackendId() {
        return backendId;
    }

    public void setBackendId(String backendId) {
        this.backendId = backendId;
    }

    public long getOnlineTime() {
        return onlineTime;
    }

    public void setOnlineTime(long onlineTime) {
        this.onlineTime = onlineTime;
    }

    /**
     * 检查是否可以接受新会话
     */
    public boolean canAcceptSession() {
        return status == AgentStatus.ONLINE && currentSessions < maxSessions;
    }

    /**
     * 增加会话数
     */
    public synchronized void incrementSessions() {
        this.currentSessions++;
    }

    /**
     * 减少会话数
     */
    public synchronized void decrementSessions() {
        if (this.currentSessions > 0) {
            this.currentSessions--;
        }
    }

    @Override
    public String toString() {
        return "Agent{" +
                "agentId='" + agentId + '\'' +
                ", agentName='" + agentName + '\'' +
                ", status=" + status +
                ", maxSessions=" + maxSessions +
                ", currentSessions=" + currentSessions +
                ", backendId='" + backendId + '\'' +
                '}';
    }
}
