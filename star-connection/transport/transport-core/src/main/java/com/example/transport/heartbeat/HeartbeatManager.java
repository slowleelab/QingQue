package com.example.transport.heartbeat;

/**
 * 心跳管理器接口
 */
public interface HeartbeatManager {

    /**
     * 启动心跳
     */
    void start();

    /**
     * 停止心跳
     */
    void stop();

    /**
     * 获取心跳配置
     */
    HeartbeatConfig getConfig();

    /**
     * 设置心跳监听器
     */
    void setHeartbeatListener(HeartbeatListener listener);

    /**
     * 获取最后一次心跳时间
     */
    long getLastHeartbeatTime(String connectionId);

    /**
     * 获取丢失的心跳数
     */
    int getMissedHeartbeats(String connectionId);

    /**
     * 获取发送的心跳总数
     */
    long getTotalHeartbeatsSent();

    /**
     * 获取收到的心跳响应总数
     */
    long getTotalHeartbeatsReceived();

    /**
     * 心跳监听器接口
     */
    interface HeartbeatListener {
        /**
         * 心跳发送时调用
         */
        default void onHeartbeatSent(String connectionId) {}

        /**
         * 心跳响应收到时调用
         */
        default void onHeartbeatReceived(String connectionId) {}

        /**
         * 心跳超时时调用
         */
        default void onHeartbeatTimeout(String connectionId) {}
    }
}
