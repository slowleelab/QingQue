package com.example.customerserver.session;

import com.example.common.model.SessionSubStatus;
import com.example.customerserver.client.LumioClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * Lumio 会话状态监听器
 * 当会话状态变更时，回调 Lumio /api/session/update 端点同步会话阶段。
 *
 * 优先使用 Session.subStatus 转换为 Lumio sub_phase；
 * 若 subStatus 未设置，回退到基于 event 的推断。
 */
@Component
public class LumioSessionListener implements SessionStateListener {

    private static final Logger LOGGER = LoggerFactory.getLogger(LumioSessionListener.class);

    private final LumioClient lumioClient;

    public LumioSessionListener(LumioClient lumioClient,
                                  SessionStateTransitionManager transitionManager) {
        this.lumioClient = lumioClient;
        transitionManager.addListener(this);
    }

    @Override
    public void onSessionAssigned(SessionLifecycleEvent event) {
        String subPhase = resolveSubPhase(event, "agent:queued", "agent:assigned");
        lumioClient.notifySessionUpdate(
                event.getSession().getSessionId(),
                "agent",
                subPhase,
                event.getAgent() != null ? event.getAgent().getAgentId() : null
        );
    }

    @Override
    public void onSessionActivated(SessionLifecycleEvent event) {
        String subPhase = resolveSubPhase(event, "agent:active", "agent:active");
        lumioClient.notifySessionUpdate(
                event.getSession().getSessionId(),
                "agent",
                subPhase,
                event.getAgent() != null ? event.getAgent().getAgentId() : null
        );
    }

    @Override
    public void onSessionClosed(SessionLifecycleEvent event) {
        String reason = mapCloseReason(event.getEvent());
        lumioClient.notifySessionUpdate(
                event.getSession().getSessionId(),
                "ended",
                null,
                event.getAgent() != null ? event.getAgent().getAgentId() : null,
                reason
        );
    }

    @Override
    public void onSessionTimeout(SessionLifecycleEvent event) {
        lumioClient.notifySessionUpdate(
                event.getSession().getSessionId(),
                "ended",
                null,
                event.getAgent() != null ? event.getAgent().getAgentId() : null,
                "timeout"
        );
    }

    @Override
    public void onSessionTransferred(SessionLifecycleEvent event) {
        String subPhase = resolveSubPhase(event, "agent:assigned", "agent:assigned");
        lumioClient.notifySessionUpdate(
                event.getSession().getSessionId(),
                "agent",
                subPhase,
                event.getAgent() != null ? event.getAgent().getAgentId() : null
        );
    }

    @Override
    public void onError(SessionLifecycleEvent event, Exception error) {
        LOGGER.error("Lumio 会话监听器异常: session={}, error={}",
                event.getSession().getSessionId(), error.getMessage());
    }

    /**
     * 优先使用 session.subStatus 转换，回退到 event 推断。
     */
    private String resolveSubPhase(SessionLifecycleEvent event,
                                    String defaultPhase,
                                    String fallbackPhase) {
        SessionSubStatus sub = event.getSession().getSubStatus();
        if (sub != null) {
            return sub.toLumioSubPhase();
        }
        // 回退: ASSIGN_AGENT → default, AGENT_ACCEPT → fallback
        if (event.getEvent() == SessionEvent.AGENT_ACCEPT) {
            return fallbackPhase;
        }
        return defaultPhase;
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
