package com.example.common.model;

/**
 * 会话子状态枚举
 *
 * 与 SessionStatus 配合使用，提供更细粒度的状态描述。
 * 对应 SmartCS SessionSubPhase:
 * - WAITING + QUEUED     → agent:queued
 * - WAITING + RINGING    → agent:assigned
 * - ACTIVE  + IN_CALL    → agent:active
 * - ACTIVE  + ON_HOLD    → agent:on_hold
 * - ACTIVE  + REVIEWING  → agent:reviewing
 * - CLOSED  (无子状态)    → ended (end_reason 区分)
 */
public enum SessionSubStatus {
    /**
     * 排队等待分配 (WAITING 下的子状态)
     */
    QUEUED("queued"),

    /**
     * 已分配坐席，等待坐席接听 (WAITING 下的子状态)
     */
    RINGING("ringing"),

    /**
     * 通话中 (ACTIVE 下的子状态)
     */
    IN_CALL("in_call"),

    /**
     * 坐席保持 (ACTIVE 下的子状态)
     */
    ON_HOLD("on_hold"),

    /**
     * 话后小结 (ACTIVE 下的子状态)
     */
    REVIEWING("reviewing");

    private final String value;

    SessionSubStatus(String value) {
        this.value = value;
    }

    public String getValue() {
        return value;
    }

    /**
     * 转换为 SmartCS sub_phase 字符串
     */
    public String toSmartcsSubPhase() {
        return switch (this) {
            case QUEUED -> "agent:queued";
            case RINGING -> "agent:assigned";
            case IN_CALL -> "agent:active";
            case ON_HOLD -> "agent:on_hold";
            case REVIEWING -> "agent:reviewing";
        };
    }
}
