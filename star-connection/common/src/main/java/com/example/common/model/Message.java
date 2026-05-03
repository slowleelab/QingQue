package com.example.common.model;

import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.Serializable;
import java.util.HashMap;
import java.util.Map;

/**
 * 节点间通信的消息实体
 *
 * <p>这是整个系统的核心消息类，用于CF（客户前置）和AB（坐席后台）节点之间的通信。
 * 所有跨节点通信都通过此消息格式进行封装。</p>
 *
 * <h3>消息结构说明：</h3>
 * <ul>
 *   <li><b>messageId</b> - 消息唯一标识，用于消息追踪和去重</li>
 *   <li><b>type</b> - 消息类型，见 {@link MessageType}</li>
 *   <li><b>source</b> - 发送方服务ID（如：router-1, agent-backend-1）</li>
 *   <li><b>target</b> - 接收方服务ID（如：router-1, agent-backend-1）</li>
 *   <li><b>payload</b> - 消息负载，JSON格式的业务数据</li>
 *   <li><b>headers</b> - 扩展消息头，用于传递元数据（如：sessionId, agentId）</li>
 * </ul>
 *
 * <h3>使用示例：</h3>
 * <pre>{@code
 * // 创建聊天消息
 * Message message = new Message(MessageType.CHAT_MESSAGE, "agent-backend-1", "router-1");
 * message.setMessageId(MessageIdGenerator.generate());
 * message.addHeader("sessionId", "session-123");
 * message.addHeader("agentId", "agent-001");
 * message.setPayloadFromObject(chatMessage);
 *
 * // 发送消息
 * channel.writeAndFlush(message);
 * }</pre>
 *
 * @author Customer Service Platform Team
 * @version 1.0.0
 * @see MessageType
 * @see ChatMessage
 * @see Session
 */
public class Message implements Serializable {

    private static final long serialVersionUID = 1L;

    /**
     * JSON序列化器，线程安全，全局共享
     */
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    /**
     * 消息唯一标识
     * <p>格式：时间戳-随机数，如：1698765432000-a1b2c3d4</p>
     */
    private String messageId;

    /**
     * 消息类型
     * @see MessageType
     */
    private MessageType type;

    /**
     * 来源服务ID
     * <p>CF节点：router-1, router-2... / AB节点：agent-backend-1, agent-backend-2...</p>
     */
    private String source;

    /**
     * 目标服务ID
     * <p>可以是具体的服务ID，也可以是"router"表示任意可用的路由节点</p>
     */
    private String target;

    /**
     * 消息创建时间戳（毫秒）
     */
    private long timestamp;

    /**
     * 消息负载（JSON字符串）
     * <p>根据消息类型不同，负载内容不同：</p>
     * <ul>
     *   <li>CHAT_MESSAGE - {@link ChatMessage} 的JSON</li>
     *   <li>SESSION_ASSIGN - {@link Session} 的JSON</li>
     *   <li>AGENT_REGISTER - {@link Agent} 的JSON</li>
     * </ul>
     */
    private String payload;

    /**
     * 附加消息头
     * <p>常用Header Key：</p>
     * <ul>
     *   <li>sessionId - 会话ID</li>
     *   <li>agentId - 坐席ID</li>
     *   <li>customerId - 客户ID</li>
     *   <li>status - 状态值</li>
     * </ul>
     */
    private Map<String, String> headers;

    /**
     * 默认构造函数
     * <p>自动设置当前时间戳和空的headers</p>
     */
    public Message() {
        this.timestamp = System.currentTimeMillis();
        this.headers = new HashMap<>();
    }

    /**
     * 便捷构造函数
     *
     * @param type   消息类型
     * @param source 发送方服务ID
     * @param target 接收方服务ID
     */
    public Message(MessageType type, String source, String target) {
        this();
        this.type = type;
        this.source = source;
        this.target = target;
    }

    // ==================== Getters & Setters ====================

    public String getMessageId() {
        return messageId;
    }

    public void setMessageId(String messageId) {
        this.messageId = messageId;
    }

    public MessageType getType() {
        return type;
    }

    public void setType(MessageType type) {
        this.type = type;
    }

    public String getSource() {
        return source;
    }

    public void setSource(String source) {
        this.source = source;
    }

    public String getTarget() {
        return target;
    }

    public void setTarget(String target) {
        this.target = target;
    }

    public long getTimestamp() {
        return timestamp;
    }

    public void setTimestamp(long timestamp) {
        this.timestamp = timestamp;
    }

    public String getPayload() {
        return payload;
    }

    public void setPayload(String payload) {
        this.payload = payload;
    }

    public Map<String, String> getHeaders() {
        return headers;
    }

    public void setHeaders(Map<String, String> headers) {
        this.headers = headers;
    }

    /**
     * 添加消息头
     *
     * @param key   消息头键名
     * @param value 消息头值
     */
    public void addHeader(String key, String value) {
        this.headers.put(key, value);
    }

    /**
     * 获取消息头
     *
     * @param key 消息头键名
     * @return 消息头值，不存在则返回null
     */
    public String getHeader(String key) {
        return this.headers.get(key);
    }

    // ==================== 负载序列化方法 ====================

    /**
     * 将JSON负载反序列化为指定类型的对象
     *
     * @param clazz 目标类型
     * @param <T>   泛型类型
     * @return 反序列化后的对象，如果payload为空则返回null
     * @throws JsonProcessingException 如果JSON解析失败
     */
    @JsonIgnore
    public <T> T getPayloadAs(Class<T> clazz) throws JsonProcessingException {
        if (payload == null || payload.isEmpty()) {
            return null;
        }
        return OBJECT_MAPPER.readValue(payload, clazz);
    }

    /**
     * 将对象序列化为JSON并设置为负载
     *
     * @param obj 要序列化的对象
     * @throws JsonProcessingException 如果序列化失败
     */
    @JsonIgnore
    public void setPayloadFromObject(Object obj) throws JsonProcessingException {
        if (obj == null) {
            this.payload = null;
        } else {
            this.payload = OBJECT_MAPPER.writeValueAsString(obj);
        }
    }

    // ==================== JSON序列化方法 ====================

    /**
     * 将消息序列化为JSON字符串
     * <p>用于网络传输</p>
     *
     * @return JSON字符串
     * @throws JsonProcessingException 如果序列化失败
     */
    public String toJson() throws JsonProcessingException {
        return OBJECT_MAPPER.writeValueAsString(this);
    }

    /**
     * 从JSON字符串反序列化消息
     *
     * @param json JSON字符串
     * @return 消息对象
     * @throws JsonProcessingException 如果解析失败
     */
    public static Message fromJson(String json) throws JsonProcessingException {
        return OBJECT_MAPPER.readValue(json, Message.class);
    }

    @Override
    public String toString() {
        return "Message{" +
                "messageId='" + messageId + '\'' +
                ", type=" + type +
                ", source='" + source + '\'' +
                ", target='" + target + '\'' +
                ", timestamp=" + timestamp +
                ", payloadLength=" + (payload != null ? payload.length() : 0) +
                ", headers=" + headers.size() +
                '}';
    }
}
