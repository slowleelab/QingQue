package com.example.transport.connection;

/**
 * 连接监听器接口
 * 监听连接状态变化
 */
public interface ConnectionListener {

    /**
     * 连接建立时调用
     */
    default void onConnected(Connection connection) {}

    /**
     * 连接关闭时调用
     */
    default void onDisconnected(Connection connection) {}

    /**
     * 连接异常时调用
     */
    default void onError(Connection connection, Throwable cause) {}

    /**
     * 收到消息时调用
     */
    default void onMessageReceived(Connection connection, Object message) {}

    /**
     * 消息发送成功时调用
     */
    default void onMessageSent(Connection connection, Object message) {}

    /**
     * 消息发送失败时调用
     */
    default void onMessageFailed(Connection connection, Object message, Throwable cause) {}
}
