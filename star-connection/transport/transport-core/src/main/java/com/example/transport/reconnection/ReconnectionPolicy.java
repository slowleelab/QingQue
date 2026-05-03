package com.example.transport.reconnection;

/**
 * 重连策略接口
 */
public interface ReconnectionPolicy {

    /**
     * 计算下次重连的延迟时间（毫秒）
     *
     * @param attemptCount 当前尝试次数
     * @return 延迟时间（毫秒）
     */
    long computeDelay(int attemptCount);

    /**
     * 是否应该继续重连
     *
     * @param attemptCount 当前尝试次数
     * @return 是否继续
     */
    boolean shouldRetry(int attemptCount);

    /**
     * 重置策略状态
     */
    void reset();

    /**
     * 获取最大重试次数
     */
    int getMaxRetries();

    /**
     * 获取当前重试次数
     */
    int getCurrentAttempt();
}
