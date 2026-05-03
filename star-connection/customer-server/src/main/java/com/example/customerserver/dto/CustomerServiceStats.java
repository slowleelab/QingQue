package com.example.customerserver.dto;

import java.util.List;

/**
 * 客服系统统计响应
 */
public class CustomerServiceStats {
    private SessionStats session;
    private AgentStats agent;
    private long timestamp;

    public CustomerServiceStats() {
        this.timestamp = System.currentTimeMillis();
    }

    public SessionStats getSession() {
        return session;
    }

    public void setSession(SessionStats session) {
        this.session = session;
    }

    public AgentStats getAgent() {
        return agent;
    }

    public void setAgent(AgentStats agent) {
        this.agent = agent;
    }

    public long getTimestamp() {
        return timestamp;
    }

    public void setTimestamp(long timestamp) {
        this.timestamp = timestamp;
    }

    /**
     * 会话统计
     */
    public static class SessionStats {
        private int total;
        private int waiting;
        private int active;
        private int closed;

        public int getTotal() {
            return total;
        }

        public void setTotal(int total) {
            this.total = total;
        }

        public int getWaiting() {
            return waiting;
        }

        public void setWaiting(int waiting) {
            this.waiting = waiting;
        }

        public int getActive() {
            return active;
        }

        public void setActive(int active) {
            this.active = active;
        }

        public int getClosed() {
            return closed;
        }

        public void setClosed(int closed) {
            this.closed = closed;
        }
    }

    /**
     * 坐席统计
     */
    public static class AgentStats {
        private int total;
        private int online;
        private int busy;
        private int offline;
        private List<AgentInfo> agents;

        public int getTotal() {
            return total;
        }

        public void setTotal(int total) {
            this.total = total;
        }

        public int getOnline() {
            return online;
        }

        public void setOnline(int online) {
            this.online = online;
        }

        public int getBusy() {
            return busy;
        }

        public void setBusy(int busy) {
            this.busy = busy;
        }

        public int getOffline() {
            return offline;
        }

        public void setOffline(int offline) {
            this.offline = offline;
        }

        public List<AgentInfo> getAgents() {
            return agents;
        }

        public void setAgents(List<AgentInfo> agents) {
            this.agents = agents;
        }
    }

    /**
     * 坐席信息
     */
    public static class AgentInfo {
        private String agentId;
        private String agentName;
        private String status;
        private int currentSessions;
        private int maxSessions;
        private String backendId;

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

        public int getCurrentSessions() {
            return currentSessions;
        }

        public void setCurrentSessions(int currentSessions) {
            this.currentSessions = currentSessions;
        }

        public int getMaxSessions() {
            return maxSessions;
        }

        public void setMaxSessions(int maxSessions) {
            this.maxSessions = maxSessions;
        }

        public String getBackendId() {
            return backendId;
        }

        public void setBackendId(String backendId) {
            this.backendId = backendId;
        }
    }
}
