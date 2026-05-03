package com.example.customerserver.session;

import com.example.common.model.SessionStatus;

/**
 * 会话状态机
 * 管理会话状态转换和生命周期事件
 */
public class SessionStateMachine {

    /**
     * 检查状态转换是否有效
     */
    public static boolean canTransition(SessionStatus from, SessionStatus to) {
        if (from == to) {
            return true;
        }

        switch (from) {
            case WAITING:
                // 等待状态可以转换到活跃或关闭
                return to == SessionStatus.ACTIVE || to == SessionStatus.CLOSED;

            case ACTIVE:
                // 活跃状态只能转换到关闭
                return to == SessionStatus.CLOSED;

            case CLOSED:
                // 关闭状态不能再转换
                return false;

            default:
                return false;
        }
    }

    /**
     * 根据事件获取目标状态
     * @param currentStatus 当前状态
     * @param event 触发事件
     * @return 目标状态，如果转换无效返回null
     */
    public static SessionStatus getTargetStatus(SessionStatus currentStatus, SessionEvent event) {
        switch (currentStatus) {
            case WAITING:
                return handleWaitingState(event);

            case ACTIVE:
                return handleActiveState(event);

            case CLOSED:
                return handleClosedState(event);

            default:
                return null;
        }
    }

    /**
     * 处理等待状态的事件
     */
    private static SessionStatus handleWaitingState(SessionEvent event) {
        switch (event) {
            case ASSIGN_AGENT:
            case AGENT_ACCEPT:
            case CUSTOMER_MESSAGE:
            case AGENT_MESSAGE:
                return SessionStatus.ACTIVE;

            case CUSTOMER_DISCONNECT:
            case TIMEOUT:
            case CLOSE:
                return SessionStatus.CLOSED;

            case CREATE:
            case AGENT_REJECT:
            case AGENT_DISCONNECT:
            case TRANSFER:
            case REASSIGN:
                // 这些事件不改变状态
                return SessionStatus.WAITING;

            default:
                return null;
        }
    }

    /**
     * 处理活跃状态的事件
     */
    private static SessionStatus handleActiveState(SessionEvent event) {
        switch (event) {
            case CUSTOMER_DISCONNECT:
            case AGENT_DISCONNECT:
            case TIMEOUT:
            case CLOSE:
                return SessionStatus.CLOSED;

            case CUSTOMER_MESSAGE:
            case AGENT_MESSAGE:
            case TRANSFER:
            case REASSIGN:
            case ASSIGN_AGENT:
            case AGENT_ACCEPT:
            case AGENT_REJECT:
            case CREATE:
                // 这些事件不改变活跃状态
                return SessionStatus.ACTIVE;

            default:
                return null;
        }
    }

    /**
     * 处理关闭状态的事件
     */
    private static SessionStatus handleClosedState(SessionEvent event) {
        // 关闭状态不接受任何事件
        return null;
    }

    /**
     * 验证事件是否可以在当前状态下执行
     */
    public static boolean canHandleEvent(SessionStatus currentStatus, SessionEvent event) {
        return getTargetStatus(currentStatus, event) != null;
    }

    /**
     * 获取状态的有效事件列表
     */
    public static SessionEvent[] getValidEvents(SessionStatus status) {
        switch (status) {
            case WAITING:
                return new SessionEvent[]{
                    SessionEvent.ASSIGN_AGENT,
                    SessionEvent.AGENT_ACCEPT,
                    SessionEvent.AGENT_REJECT,
                    SessionEvent.CUSTOMER_MESSAGE,
                    SessionEvent.CUSTOMER_DISCONNECT,
                    SessionEvent.TIMEOUT,
                    SessionEvent.CLOSE,
                    SessionEvent.REASSIGN
                };

            case ACTIVE:
                return new SessionEvent[]{
                    SessionEvent.CUSTOMER_MESSAGE,
                    SessionEvent.AGENT_MESSAGE,
                    SessionEvent.CUSTOMER_DISCONNECT,
                    SessionEvent.AGENT_DISCONNECT,
                    SessionEvent.TIMEOUT,
                    SessionEvent.CLOSE,
                    SessionEvent.TRANSFER,
                    SessionEvent.REASSIGN
                };

            case CLOSED:
                return new SessionEvent[]{};

            default:
                return new SessionEvent[]{};
        }
    }

    /**
     * 获取状态显示名称
     */
    public static String getStatusDisplayName(SessionStatus status) {
        switch (status) {
            case WAITING:
                return "等待中";
            case ACTIVE:
                return "进行中";
            case CLOSED:
                return "已关闭";
            default:
                return "未知";
        }
    }

    /**
     * 获取事件显示名称
     */
    public static String getEventDisplayName(SessionEvent event) {
        switch (event) {
            case CREATE:
                return "创建会话";
            case ASSIGN_AGENT:
                return "分配坐席";
            case AGENT_ACCEPT:
                return "坐席接受";
            case AGENT_REJECT:
                return "坐席拒绝";
            case CUSTOMER_MESSAGE:
                return "客户消息";
            case AGENT_MESSAGE:
                return "坐席消息";
            case CUSTOMER_DISCONNECT:
                return "客户断开";
            case AGENT_DISCONNECT:
                return "坐席断开";
            case TIMEOUT:
                return "会话超时";
            case CLOSE:
                return "关闭会话";
            case TRANSFER:
                return "转接会话";
            case REASSIGN:
                return "重新分配";
            default:
                return "未知事件";
        }
    }
}
