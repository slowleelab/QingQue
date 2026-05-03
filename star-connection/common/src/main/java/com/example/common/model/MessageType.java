package com.example.common.model;

/**
 * 消息类型枚举
 *
 * <p>定义了系统中所有消息类型，用于标识消息的用途和处理方式。
 * 消息类型分为两大类：基础通信类型（1-9）和业务类型（10+）。</p>
 *
 * <h3>消息类型分类：</h3>
 *
 * <h4>基础通信类型（1-9）：</h4>
 * <ul>
 *   <li>{@link #REGISTER} - 客户端注册，建立连接后首先发送</li>
 *   <li>{@link #HEARTBEAT} - 心跳消息，保持连接活跃</li>
 *   <li>{@link #REQUEST} - 请求消息，需要响应</li>
 *   <li>{@link #RESPONSE} - 响应消息，对应REQUEST的回复</li>
 *   <li>{@link #NOTIFY} - 通知消息，不需要响应</li>
 *   <li>{@link #AUTH} - 认证消息，用于身份验证</li>
 * </ul>
 *
 * <h4>业务消息类型（10+）：</h4>
 * <ul>
 *   <li>{@link #SESSION_CREATE} - 创建会话，客户进线时发送</li>
 *   <li>{@link #SESSION_ASSIGN} - 分配会话，坐席接手时发送</li>
 *   <li>{@link #SESSION_CLOSE} - 关闭会话，会话结束时发送</li>
 *   <li>{@link #AGENT_REGISTER} - 坐席注册，坐席上线时发送</li>
 *   <li>{@link #AGENT_STATUS} - 坐席状态更新</li>
 *   <li>{@link #CHAT_MESSAGE} - 聊天消息，客户与坐席对话</li>
 * </ul>
 *
 * <h3>消息流程示例：</h3>
 * <pre>
 * 1. AB启动连接CF：
 *    AB → CF: REGISTER (携带backendId)
 *    CF → AB: RESPONSE (确认注册)
 *
 * 2. 坐席上线：
 *    坐席WebSocket → AB: AGENT_REGISTER
 *    AB → CF: AGENT_REGISTER (转发)
 *    CF 注册到ZK: /agent-bindings/{agentId} → {backendId}
 *
 * 3. 客户进线：
 *    客户WebSocket → CF: 创建会话
 *    CF → ZK: 注册 /customer-bindings/{customerId} → {routerId}
 *    CF → AB: SESSION_CREATE
 *    CF 分配坐席
 *    CF → AB: SESSION_ASSIGN
 *    AB → 坐席WebSocket: SESSION_ASSIGN
 *
 * 4. 客户发送消息：
 *    客户 → CF: CHAT_MESSAGE
 *    CF 查询 agentId → backendId
 *    CF → AB: CHAT_MESSAGE
 *    AB → 坐席: CHAT_MESSAGE
 *
 * 5. 坐席回复消息：
 *    坐席 → AB: CHAT_MESSAGE
 *    AB 查询 sessionId → routerId
 *    AB → CF: CHAT_MESSAGE (目标为具体routerId)
 *    CF → 客户: CHAT_MESSAGE
 * </pre>
 *
 * @author Customer Service Platform Team
 * @version 1.0.0
 * @see Message
 */
public enum MessageType {

    // ==================== 基础通信类型 (1-9) ====================

    /**
     * 客户端注册消息
     * <p>AB节点连接到CF后首先发送此消息，携带backendId。</p>
     * <p>消息头：backendId - 后台节点ID</p>
     */
    REGISTER(1),

    /**
     * 心跳消息
     * <p>用于检测连接是否存活，默认每20秒发送一次。</p>
     * <p>超过3次未响应则认为连接断开，触发重连。</p>
     */
    HEARTBEAT(2),

    /**
     * 请求消息
     * <p>需要响应的请求，发送方等待RESPONSE。</p>
     */
    REQUEST(3),

    /**
     * 响应消息
     * <p>对REQUEST的响应，携带原始请求的messageId。</p>
     */
    RESPONSE(4),

    /**
     * 通知消息
     * <p>单向通知，不需要响应。</p>
     */
    NOTIFY(5),

    /**
     * 认证消息
     * <p>用于身份验证，可携带token等认证信息。</p>
     */
    AUTH(6),

    // ==================== 在线客服系统消息类型 (10+) ====================

    /**
     * 创建会话
     * <p>客户进线时，CF创建会话并通知AB。</p>
     * <p>负载：{@link Session} JSON</p>
     * <p>消息头：customerId, routerId</p>
     */
    SESSION_CREATE(10),

    /**
     * 分配会话给坐席
     * <p>会话分配坐席后，CF通知AB和坐席。</p>
     * <p>负载：{@link Session} JSON（包含agentId, routerId, backendId）</p>
     * <p>消息头：sessionId, agentId</p>
     *
     * <p>重要：此消息携带完整的Session信息，AB收到后保存到本地SessionStore，
     * 用于后续坐席回复时查找routerId。</p>
     */
    SESSION_ASSIGN(11),

    /**
     * 关闭会话
     * <p>会话结束时发送，可以是客户断开、坐席关闭或超时。</p>
     * <p>消息头：sessionId</p>
     */
    SESSION_CLOSE(12),

    /**
     * 坐席注册
     * <p>坐席上线时，AB发送给CF注册坐席信息。</p>
     * <p>负载：{@link Agent} JSON</p>
     * <p>CF收到后注册到ZK: /agent-bindings/{agentId} → {backendId}</p>
     */
    AGENT_REGISTER(13),

    /**
     * 坐席状态更新
     * <p>坐席状态变化时发送（上线、忙碌、离线）。</p>
     * <p>消息头：agentId, status</p>
     */
    AGENT_STATUS(14),

    /**
     * 聊天消息
     * <p>客户与坐席之间的对话消息。</p>
     * <p>负载：{@link ChatMessage} JSON</p>
     * <p>消息头：sessionId, agentId, customerId</p>
     *
     * <h4>消息路由：</h4>
     * <ul>
     *   <li>客户→坐席：CF查询 agentId → backendId，发送到对应AB</li>
     *   <li>坐席→客户：AB查询 sessionId → routerId，发送到对应CF</li>
     * </ul>
     */
    CHAT_MESSAGE(15);

    /**
     * 消息类型编码
     */
    private final int code;

    MessageType(int code) {
        this.code = code;
    }

    /**
     * 获取消息类型编码
     *
     * @return 消息类型编码
     */
    public int getCode() {
        return code;
    }

    /**
     * 根据编码获取消息类型
     *
     * @param code 消息类型编码
     * @return 对应的消息类型
     * @throws IllegalArgumentException 如果编码不存在
     */
    public static MessageType fromCode(int code) {
        for (MessageType type : values()) {
            if (type.code == code) {
                return type;
            }
        }
        throw new IllegalArgumentException("Unknown message type code: " + code);
    }
}
