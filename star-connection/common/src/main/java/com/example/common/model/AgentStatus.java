package com.example.common.model;

/**
 * 坐席状态枚举
 */
public enum AgentStatus {
    /**
     * 离线
     */
    OFFLINE(0),

    /**
     * 在线空闲
     */
    ONLINE(1),

    /**
     * 忙碌
     */
    BUSY(2);

    private final int code;

    AgentStatus(int code) {
        this.code = code;
    }

    public int getCode() {
        return code;
    }

    public static AgentStatus fromCode(int code) {
        for (AgentStatus status : values()) {
            if (status.code == code) {
                return status;
            }
        }
        throw new IllegalArgumentException("Unknown agent status code: " + code);
    }
}
