package com.example.transport.heartbeat;

/**
 * 心跳配置
 */
public class HeartbeatConfig {

    /**
     * 心跳间隔（秒）
     */
    private int intervalSeconds = 20;

    /**
     * 心跳超时时间（秒）
     */
    private int timeoutSeconds = 60;

    /**
     * 最大丢失心跳数
     */
    private int maxMissedHeartbeats = 3;

    /**
     * 是否启用心跳
     */
    private boolean enabled = true;

    public HeartbeatConfig() {
    }

    public HeartbeatConfig(int intervalSeconds, int timeoutSeconds) {
        this.intervalSeconds = intervalSeconds;
        this.timeoutSeconds = timeoutSeconds;
    }

    public int getIntervalSeconds() {
        return intervalSeconds;
    }

    public void setIntervalSeconds(int intervalSeconds) {
        this.intervalSeconds = intervalSeconds;
    }

    public int getTimeoutSeconds() {
        return timeoutSeconds;
    }

    public void setTimeoutSeconds(int timeoutSeconds) {
        this.timeoutSeconds = timeoutSeconds;
    }

    public int getMaxMissedHeartbeats() {
        return maxMissedHeartbeats;
    }

    public void setMaxMissedHeartbeats(int maxMissedHeartbeats) {
        this.maxMissedHeartbeats = maxMissedHeartbeats;
    }

    public boolean isEnabled() {
        return enabled;
    }

    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
    }
}
