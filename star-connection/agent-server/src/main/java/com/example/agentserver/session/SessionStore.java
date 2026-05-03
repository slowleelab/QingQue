package com.example.agentserver.session;

import com.example.common.model.Session;
import com.example.common.model.SessionStatus;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

/**
 * AB 端会话存储
 * 保存从 CF 同步过来的会话信息
 */
@Component
public class SessionStore {
    private static final Logger LOGGER = LoggerFactory.getLogger(SessionStore.class);

    // sessionId -> Session
    private final Map<String, Session> sessions = new ConcurrentHashMap<>();

    // customerId -> sessionId (用于快速查找)
    private final Map<String, String> customerSessionMap = new ConcurrentHashMap<>();

    /**
     * 保存会话
     */
    public void save(Session session) {
        if (session == null || session.getSessionId() == null) {
            return;
        }
        sessions.put(session.getSessionId(), session);

        // 更新客户ID映射
        if (session.getCustomerId() != null) {
            customerSessionMap.put(session.getCustomerId(), session.getSessionId());
        }

        LOGGER.debug("会话已保存: sessionId={}, customerId={}, routerId={}",
                session.getSessionId(), session.getCustomerId(), session.getRouterId());
    }

    /**
     * 根据会话ID获取会话
     */
    public Optional<Session> findById(String sessionId) {
        if (sessionId == null) {
            return Optional.empty();
        }
        return Optional.ofNullable(sessions.get(sessionId));
    }

    /**
     * 根据客户ID获取会话
     */
    public Optional<Session> findByCustomerId(String customerId) {
        if (customerId == null) {
            return Optional.empty();
        }
        String sessionId = customerSessionMap.get(customerId);
        if (sessionId != null) {
            return findById(sessionId);
        }
        return Optional.empty();
    }

    /**
     * 删除会话
     */
    public void delete(String sessionId) {
        Session session = sessions.remove(sessionId);
        if (session != null && session.getCustomerId() != null) {
            customerSessionMap.remove(session.getCustomerId());
        }
        LOGGER.debug("会话已删除: sessionId={}", sessionId);
    }

    /**
     * 更新会话状态
     */
    public void updateStatus(String sessionId, SessionStatus status) {
        Session session = sessions.get(sessionId);
        if (session != null) {
            session.setStatus(status);
            LOGGER.debug("会话状态更新: sessionId={}, status={}", sessionId, status);
        }
    }

    /**
     * 获取所有活跃会话
     */
    public Map<String, Session> getAllSessions() {
        return new ConcurrentHashMap<>(sessions);
    }

    /**
     * 获取会话数量
     */
    public int size() {
        return sessions.size();
    }

    /**
     * 清空所有会话
     */
    public void clear() {
        sessions.clear();
        customerSessionMap.clear();
        LOGGER.info("所有会话已清空");
    }
}
