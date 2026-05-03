package com.example.customerserver.session;

/**
 * 会话状态监听器接口
 * 用于监听会话生命周期事件
 */
public interface SessionStateListener {

    /**
     * 会话状态变更
     */
    default void onStateChange(SessionLifecycleEvent event) {}

    /**
     * 会话创建
     */
    default void onSessionCreated(SessionLifecycleEvent event) {}

    /**
     * 会话分配坐席
     */
    default void onSessionAssigned(SessionLifecycleEvent event) {}

    /**
     * 会话激活
     */
    default void onSessionActivated(SessionLifecycleEvent event) {}

    /**
     * 会话关闭
     */
    default void onSessionClosed(SessionLifecycleEvent event) {}

    /**
     * 会话超时
     */
    default void onSessionTimeout(SessionLifecycleEvent event) {}

    /**
     * 会话转接
     */
    default void onSessionTransferred(SessionLifecycleEvent event) {}

    /**
     * 错误处理
     */
    default void onError(SessionLifecycleEvent event, Exception error) {}
}
