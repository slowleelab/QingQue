package com.example.customerserver.session;

import com.example.common.model.Agent;
import com.example.common.model.Session;
import com.example.common.model.SessionStatus;
import com.example.customerserver.session.SessionTransitionResult.TransitionStatus;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * 会话状态转换管理器
 * 管理状态转换和监听器通知
 */
@Component
public class SessionStateTransitionManager {
    private static final Logger LOGGER = LoggerFactory.getLogger(SessionStateTransitionManager.class);

    private final List<SessionStateListener> listeners = new CopyOnWriteArrayList<>();

    /**
     * 添加状态监听器
     */
    public void addListener(SessionStateListener listener) {
        if (!listeners.contains(listener)) {
            listeners.add(listener);
            LOGGER.debug("添加会话状态监听器: {}", listener.getClass().getSimpleName());
        }
    }

    /**
     * 移除状态监听器
     */
    public void removeListener(SessionStateListener listener) {
        listeners.remove(listener);
        LOGGER.debug("移除会话状态监听器: {}", listener.getClass().getSimpleName());
    }

    /**
     * 执行状态转换
     */
    public SessionTransitionResult transition(Session session, SessionEvent event) {
        return transition(session, event, null, null);
    }

    /**
     * 执行状态转换（带坐席信息）
     */
    public SessionTransitionResult transition(Session session, SessionEvent event,
                                               Agent agent, String message) {
        SessionStatus previousStatus = session.getStatus();

        // 验证事件是否可以处理
        if (!SessionStateMachine.canHandleEvent(previousStatus, event)) {
            LOGGER.warn("事件 {} 在状态 {} 下无法处理，会话: {}",
                    event, previousStatus, session.getSessionId());
            return SessionTransitionResult.invalidTransition(previousStatus, event);
        }

        // 获取目标状态
        SessionStatus newStatus = SessionStateMachine.getTargetStatus(previousStatus, event);

        if (newStatus == null) {
            LOGGER.warn("无法确定事件 {} 在状态 {} 下的目标状态，会话: {}",
                    event, previousStatus, session.getSessionId());
            return SessionTransitionResult.invalidTransition(previousStatus, event);
        }

        // 检查状态转换是否有效
        if (!SessionStateMachine.canTransition(previousStatus, newStatus)) {
            LOGGER.warn("无效的状态转换: {} -> {}，会话: {}",
                    previousStatus, newStatus, session.getSessionId());
            return SessionTransitionResult.invalidTransition(previousStatus, event);
        }

        // 创建生命周期事件
        SessionLifecycleEvent lifecycleEvent = new SessionLifecycleEvent(
                session, previousStatus, newStatus, event, agent, message);

        // 执行状态转换
        session.setStatus(newStatus);
        session.touch();

        LOGGER.info("会话状态转换: {} -> {} (事件: {})，会话: {}",
                previousStatus, newStatus, event, session.getSessionId());

        // 通知监听器
        notifyListeners(lifecycleEvent);

        return SessionTransitionResult.success(previousStatus, newStatus, event);
    }

    /**
     * 通知所有监听器
     */
    private void notifyListeners(SessionLifecycleEvent event) {
        for (SessionStateListener listener : listeners) {
            try {
                // 通知通用状态变更
                listener.onStateChange(event);

                // 通知特定事件
                switch (event.getEvent()) {
                    case CREATE:
                        listener.onSessionCreated(event);
                        break;
                    case ASSIGN_AGENT:
                    case AGENT_ACCEPT:
                        if (event.isStateChanged()) {
                            listener.onSessionAssigned(event);
                        }
                        break;
                    case CUSTOMER_MESSAGE:
                    case AGENT_MESSAGE:
                        if (event.isStateChanged() && event.getNewStatus() == SessionStatus.ACTIVE) {
                            listener.onSessionActivated(event);
                        }
                        break;
                    case CUSTOMER_DISCONNECT:
                    case AGENT_DISCONNECT:
                    case CLOSE:
                        listener.onSessionClosed(event);
                        break;
                    case TIMEOUT:
                        listener.onSessionTimeout(event);
                        break;
                    case TRANSFER:
                    case REASSIGN:
                        listener.onSessionTransferred(event);
                        break;
                    default:
                        break;
                }
            } catch (Exception e) {
                LOGGER.error("监听器处理事件失败: {}, 监听器: {}",
                        event, listener.getClass().getSimpleName(), e);
                listener.onError(event, e);
            }
        }
    }

    /**
     * 获取监听器数量
     */
    public int getListenerCount() {
        return listeners.size();
    }
}
