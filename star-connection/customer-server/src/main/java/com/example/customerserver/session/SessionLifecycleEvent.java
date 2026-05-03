package com.example.customerserver.session;

import com.example.common.model.Agent;
import com.example.common.model.Session;
import com.example.common.model.SessionStatus;

/**
 * 会话生命周期事件
 */
public class SessionLifecycleEvent {
    private final Session session;
    private final SessionStatus previousStatus;
    private final SessionStatus newStatus;
    private final SessionEvent event;
    private final Agent agent;
    private final String message;
    private final long timestamp;

    public SessionLifecycleEvent(Session session, SessionStatus previousStatus,
                                  SessionStatus newStatus, SessionEvent event) {
        this(session, previousStatus, newStatus, event, null, null);
    }

    public SessionLifecycleEvent(Session session, SessionStatus previousStatus,
                                  SessionStatus newStatus, SessionEvent event,
                                  Agent agent, String message) {
        this.session = session;
        this.previousStatus = previousStatus;
        this.newStatus = newStatus;
        this.event = event;
        this.agent = agent;
        this.message = message;
        this.timestamp = System.currentTimeMillis();
    }

    public Session getSession() {
        return session;
    }

    public SessionStatus getPreviousStatus() {
        return previousStatus;
    }

    public SessionStatus getNewStatus() {
        return newStatus;
    }

    public SessionEvent getEvent() {
        return event;
    }

    public Agent getAgent() {
        return agent;
    }

    public String getMessage() {
        return message;
    }

    public long getTimestamp() {
        return timestamp;
    }

    public boolean isStateChanged() {
        return previousStatus != newStatus;
    }

    @Override
    public String toString() {
        return "SessionLifecycleEvent{" +
                "sessionId='" + session.getSessionId() + '\'' +
                ", previousStatus=" + previousStatus +
                ", newStatus=" + newStatus +
                ", event=" + event +
                ", agentId='" + (agent != null ? agent.getAgentId() : null) + '\'' +
                ", timestamp=" + timestamp +
                '}';
    }
}
