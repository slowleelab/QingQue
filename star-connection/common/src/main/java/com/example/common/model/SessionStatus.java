package com.example.common.model;

/**
 * 会话状态枚举
 */
public enum SessionStatus {
    /**
     * 等待分配坐席
     */
    WAITING(1),

    /**
     * 会话进行中
     */
    ACTIVE(2),

    /**
     * 会话已关闭
     */
    CLOSED(3);

    private final int code;

    SessionStatus(int code) {
        this.code = code;
    }

    public int getCode() {
        return code;
    }

    public static SessionStatus fromCode(int code) {
        for (SessionStatus status : values()) {
            if (status.code == code) {
                return status;
            }
        }
        throw new IllegalArgumentException("Unknown session status code: " + code);
    }
}
