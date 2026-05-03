package com.example.agentserver.agent;

import com.example.common.model.AgentStatus;

/**
 * 坐席状态管理器
 */
public class AgentStatusManager {
    private volatile AgentStatus status = AgentStatus.OFFLINE;

    public AgentStatus getStatus() {
        return status;
    }

    public void setStatus(AgentStatus status) {
        this.status = status;
    }

    public boolean isOnline() {
        return status == AgentStatus.ONLINE || status == AgentStatus.BUSY;
    }

    public boolean canAcceptSession() {
        return status == AgentStatus.ONLINE;
    }
}
