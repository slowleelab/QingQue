package com.example.agentserver.agent;

import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * 坐席会话注册表
 * 管理坐席与客户会话的对应关系
 */
@Component
public class AgentSessionRegistry {
    // 坐席ID -> 会话ID列表
    private final Map<String, List<String>> agentSessions = new ConcurrentHashMap<>();
    // 会话ID -> 坐席ID
    private final Map<String, String> sessionToAgent = new ConcurrentHashMap<>();

    /**
     * 注册会话
     */
    public void registerSession(String agentId, String sessionId) {
        agentSessions.computeIfAbsent(agentId, k -> new CopyOnWriteArrayList<>()).add(sessionId);
        sessionToAgent.put(sessionId, agentId);
    }

    /**
     * 注销会话
     */
    public void unregisterSession(String agentId, String sessionId) {
        List<String> sessions = agentSessions.get(agentId);
        if (sessions != null) {
            sessions.remove(sessionId);
            if (sessions.isEmpty()) {
                agentSessions.remove(agentId);
            }
        }
        sessionToAgent.remove(sessionId);
    }

    /**
     * 获取坐席的会话列表
     */
    public List<String> getSessions(String agentId) {
        List<String> sessions = agentSessions.get(agentId);
        return sessions != null ? List.copyOf(sessions) : List.of();
    }

    /**
     * 获取会话对应的坐席
     */
    public String getAgentForSession(String sessionId) {
        return sessionToAgent.get(sessionId);
    }

    /**
     * 获取坐席的会话数量
     */
    public int getSessionCount(String agentId) {
        List<String> sessions = agentSessions.get(agentId);
        return sessions != null ? sessions.size() : 0;
    }

    /**
     * 获取所有在线坐席
     */
    public Set<String> getOnlineAgents() {
        return agentSessions.keySet();
    }

    /**
     * 清除坐席的所有会话
     */
    public void clearAgentSessions(String agentId) {
        List<String> sessions = agentSessions.remove(agentId);
        if (sessions != null) {
            for (String sessionId : sessions) {
                sessionToAgent.remove(sessionId);
            }
        }
    }
}
