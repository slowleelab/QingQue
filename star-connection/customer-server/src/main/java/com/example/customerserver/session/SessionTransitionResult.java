package com.example.customerserver.session;

import com.example.common.model.SessionStatus;

/**
 * 会话状态转换结果
 */
public class SessionTransitionResult {

    public enum TransitionStatus {
        /**
         * 转换成功
         */
        SUCCESS,

        /**
         * 无效转换（事件不能在当前状态执行）
         */
        INVALID,

        /**
         * 状态未改变
         */
        NO_CHANGE
    }

    private final TransitionStatus status;
    private final SessionStatus previousStatus;
    private final SessionStatus newStatus;
    private final SessionEvent event;
    private final String errorMessage;

    private SessionTransitionResult(TransitionStatus status, SessionStatus previousStatus,
                                     SessionStatus newStatus, SessionEvent event, String errorMessage) {
        this.status = status;
        this.previousStatus = previousStatus;
        this.newStatus = newStatus;
        this.event = event;
        this.errorMessage = errorMessage;
    }

    /**
     * 创建成功结果
     */
    public static SessionTransitionResult success(SessionStatus previousStatus,
                                                   SessionStatus newStatus, SessionEvent event) {
        return new SessionTransitionResult(
                TransitionStatus.SUCCESS, previousStatus, newStatus, event, null);
    }

    /**
     * 创建无效转换结果
     */
    public static SessionTransitionResult invalidTransition(SessionStatus currentStatus, SessionEvent event) {
        return new SessionTransitionResult(
                TransitionStatus.INVALID, currentStatus, currentStatus, event,
                "Invalid transition for event " + event + " in state " + currentStatus);
    }

    /**
     * 创建状态未改变结果
     */
    public static SessionTransitionResult noChange(SessionStatus status, SessionEvent event) {
        return new SessionTransitionResult(
                TransitionStatus.NO_CHANGE, status, status, event, null);
    }

    public boolean isSuccess() {
        return status == TransitionStatus.SUCCESS;
    }

    public boolean isInvalid() {
        return status == TransitionStatus.INVALID;
    }

    public boolean isStateChanged() {
        return previousStatus != newStatus;
    }

    public TransitionStatus getStatus() {
        return status;
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

    public String getErrorMessage() {
        return errorMessage;
    }

    @Override
    public String toString() {
        return "SessionTransitionResult{" +
                "status=" + status +
                ", previousStatus=" + previousStatus +
                ", newStatus=" + newStatus +
                ", event=" + event +
                (errorMessage != null ? ", errorMessage='" + errorMessage + '\'' : "") +
                '}';
    }
}
