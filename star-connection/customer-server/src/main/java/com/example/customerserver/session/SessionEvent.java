package com.example.customerserver.session;

/**
 * 会话事件枚举
 */
public enum SessionEvent {
    /**
     * 创建会话
     */
    CREATE,

    /**
     * 分配坐席
     */
    ASSIGN_AGENT,

    /**
     * 坐席接受
     */
    AGENT_ACCEPT,

    /**
     * 坐席拒绝
     */
    AGENT_REJECT,

    /**
     * 客户发送消息
     */
    CUSTOMER_MESSAGE,

    /**
     * 坐席发送消息
     */
    AGENT_MESSAGE,

    /**
     * 客户断开连接
     */
    CUSTOMER_DISCONNECT,

    /**
     * 坐席断开连接
     */
    AGENT_DISCONNECT,

    /**
     * 会话超时
     */
    TIMEOUT,

    /**
     * 手动关闭
     */
    CLOSE,

    /**
     * 转接
     */
    TRANSFER,

    /**
     * 重新分配
     */
    REASSIGN
}
