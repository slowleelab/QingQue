package com.example.common.model;

/**
 * 发送者类型枚举
 */
public enum SenderType {
    /**
     * 客户
     */
    CUSTOMER(1),

    /**
     * 坐席
     */
    AGENT(2),

    /**
     * 系统
     */
    SYSTEM(3);

    private final int code;

    SenderType(int code) {
        this.code = code;
    }

    public int getCode() {
        return code;
    }

    public static SenderType fromCode(int code) {
        for (SenderType type : values()) {
            if (type.code == code) {
                return type;
            }
        }
        throw new IllegalArgumentException("Unknown sender type code: " + code);
    }
}
