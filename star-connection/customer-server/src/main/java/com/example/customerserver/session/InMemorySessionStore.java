package com.example.customerserver.session;

import com.example.common.model.Session;
import com.example.common.model.SessionStatus;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.stream.Collectors;

/**
 * 内存会话存储实现
 */
@Component
public class InMemorySessionStore implements SessionStore {
    private static final Logger LOGGER = LoggerFactory.getLogger(InMemorySessionStore.class);

    // 会话ID -> 会话
    private final Map<String, Session> sessions = new ConcurrentHashMap<>();
    // 客户ID -> 会话ID（一个客户同时只能有一个活跃会话）
    private final Map<String, String> customerSessions = new ConcurrentHashMap<>();

    @Override
    public void save(Session session) {
        sessions.put(session.getSessionId(), session);
        if (session.getCustomerId() != null) {
            customerSessions.put(session.getCustomerId(), session.getSessionId());
        }
        LOGGER.debug("会话已保存: {}", session.getSessionId());
    }

    @Override
    public Optional<Session> findById(String sessionId) {
        return Optional.ofNullable(sessions.get(sessionId));
    }

    @Override
    public Optional<Session> findByCustomerId(String customerId) {
        String sessionId = customerSessions.get(customerId);
        if (sessionId != null) {
            return findById(sessionId);
        }
        return Optional.empty();
    }

    @Override
    public List<Session> findByAgentId(String agentId) {
        return sessions.values().stream()
                .filter(s -> agentId.equals(s.getAgentId()))
                .collect(Collectors.toList());
    }

    @Override
    public List<Session> findByStatus(SessionStatus status) {
        return sessions.values().stream()
                .filter(s -> s.getStatus() == status)
                .collect(Collectors.toList());
    }

    @Override
    public void delete(String sessionId) {
        Session session = sessions.remove(sessionId);
        if (session != null && session.getCustomerId() != null) {
            customerSessions.remove(session.getCustomerId());
        }
        LOGGER.debug("会话已删除: {}", sessionId);
    }

    @Override
    public List<Session> findAll() {
        return List.copyOf(sessions.values());
    }

    @Override
    public int count() {
        return sessions.size();
    }

    @Override
    public int countByAgentId(String agentId) {
        return (int) sessions.values().stream()
                .filter(s -> agentId.equals(s.getAgentId()))
                .filter(s -> s.getStatus() == SessionStatus.ACTIVE)
                .count();
    }

    @Override
    public List<Session> findByTimeRange(long startTime, long endTime) {
        return sessions.values().stream()
                .filter(s -> s.getCreateTime() >= startTime && s.getCreateTime() <= endTime)
                .collect(Collectors.toList());
    }

    @Override
    public List<Session> query(String sessionId, String customerId, String agentId,
                               String status, Long startTime, Long endTime) {
        return sessions.values().stream()
                .filter(s -> sessionId == null || sessionId.isEmpty() ||
                        s.getSessionId().toLowerCase().contains(sessionId.toLowerCase()))
                .filter(s -> customerId == null || customerId.isEmpty() ||
                        (s.getCustomerId() != null && s.getCustomerId().toLowerCase().contains(customerId.toLowerCase())))
                .filter(s -> agentId == null || agentId.isEmpty() ||
                        (s.getAgentId() != null && s.getAgentId().toLowerCase().contains(agentId.toLowerCase())))
                .filter(s -> status == null || status.isEmpty() ||
                        s.getStatus().name().equalsIgnoreCase(status))
                .filter(s -> startTime == null || s.getCreateTime() >= startTime)
                .filter(s -> endTime == null || s.getCreateTime() <= endTime)
                .sorted((a, b) -> Long.compare(b.getCreateTime(), a.getCreateTime())) // 按时间倒序
                .collect(Collectors.toList());
    }
}
