package com.example.customerserver.session;

import com.example.customerserver.client.SmartcsClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * SmartCS 会话状态监听器
 * 当会话状态变更时，回调 SmartCS /api/session/update 端点同步会话阶段。
 *
 * 映射规则:
 * - WAITING → AG_QUEUED 或 AG_ASSIGNED (由 event 区分)
 * - ACTIVE  → AG_ACTIVE, AG_ON_HOLD, AG_REVIEWING
 * - CLOSED  → ENDED
 */
@Component
public class SmartcsSessionListener implements SessionStateListener {

    private static final Logger LOGGER = LoggerFactory.getLogger(SmartcsSessionListener.class);

    private final SmartcsClient smartcsClient;

    public SmartcsSessionListener(SmartcsClient smartcsClient,
                                  SessionStateTransitionManager transitionManager) {
        this.smartcsClient = smartcsClient;
        transitionManager.addListener(this);
    }

    @Override
    public void onSessionAssigned(SessionLifecycleEvent event) {
        // ASSIGN_AGENT → agent:queued, AGENT_ACCEPT → agent:assigned
        String subPhase = (event.getEvent() == SessionEvent.AGENT_ACCEPT)
                ? "agent:assigned"
                : "agent:queued";
        smartcsClient.notifySessionUpdate(
                event.getSession().getSessionId(),
                "agent",
                subPhase,
                event.getAgent() != null ? event.getAgent().getAgentId() : null
        );
    }

    @Override
    public void onSessionActivated(SessionLifecycleEvent event) {
        smartcsClient.notifySessionUpdate(
                event.getSession().getSessionId(),
                "agent",
                "agent:active",
                event.getAgent() != null ? event.getAgent().getAgentId() : null
        );
    }

    @Override
    public void onSessionClosed(SessionLifecycleEvent event) {
        String reason = mapCloseReason(event.getEvent());
        smartcsClient.notifySessionUpdate(
                event.getSession().getSessionId(),
                "ended",
                null,
                event.getAgent() != null ? event.getAgent().getAgentId() : null,
                reason
        );
    }

    @Override
    public void onSessionTimeout(SessionLifecycleEvent event) {
        smartcsClient.notifySessionUpdate(
                event.getSession().getSessionId(),
                "ended",
                null,
                event.getAgent() != null ? event.getAgent().getAgentId() : null,
                "timeout"
        );
    }

    @Override
    public void onSessionTransferred(SessionLifecycleEvent event) {
        // 转接回到 agent:assigned 状态（新坐席待接听）
        smartcsClient.notifySessionUpdate(
                event.getSession().getSessionId(),
                "agent",
                "agent:assigned",
                event.getAgent() != null ? event.getAgent().getAgentId() : null
        );
    }

    @Override
    public void onError(SessionLifecycleEvent event, Exception error) {
        LOGGER.error("SmartCS 会话监听器异常: session={}, error={}",
                event.getSession().getSessionId(), error.getMessage());
    }

    private String mapCloseReason(SessionEvent event) {
        return switch (event) {
            case CUSTOMER_DISCONNECT -> "cust_disconnect";
            case AGENT_DISCONNECT -> "agent_disconnect";
            case TIMEOUT -> "timeout";
            case CLOSE -> "completed";
            default -> "system_error";
        };
    }
}
