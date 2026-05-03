package com.example.common.model;

import java.io.Serializable;

/**
 * 会话实体
 *
 * <p>会话（Session）是客服系统的核心概念，表示一个客户与坐席之间的对话关系。
 * 每个会话都有唯一的ID，并记录客户信息、坐席信息和会话状态。</p>
 *
 * <h3>会话生命周期：</h3>
 * <pre>
 *                    ┌──────────────┐
 *                    │   创建会话    │
 *                    └──────┬───────┘
 *                           │
 *                           ▼
 *     ┌──────────────┐  ┌──────────────┐
 *     │   等待中      │◄─│   WAITING   │
 *     └──────┬───────┘  └──────────────┘
 *            │
 *            │ 分配坐席
 *            ▼
 *     ┌──────────────┐  ┌──────────────┐
 *     │   进行中      │◄─│   ACTIVE    │
 *     └──────┬───────┘  └──────────────┘
 *            │
 *            │ 关闭会话
 *            ▼
 *     ┌──────────────┐  ┌──────────────┐
 *     │   已关闭      │◄─│   CLOSED    │
 *     └──────────────┘  └──────────────┘
 * </pre>
 *
 * <h3>关键字段说明：</h3>
 * <ul>
 *   <li><b>sessionId</b> - 会话唯一标识，格式：session-{uuid}</li>
 *   <li><b>customerId</b> - 客户唯一标识</li>
 *   <li><b>agentId</b> - 分配的坐席ID，WAITING状态时为null</li>
 *   <li><b>routerId</b> - 客户连接的CF节点ID，用于坐席回复消息时路由</li>
 *   <li><b>backendId</b> - 坐席连接的AB节点ID，用于客户消息路由</li>
 * </ul>
 *
 * <h3>消息路由机制：</h3>
 * <pre>
 * 客户→坐席：CF查询 agentId → backendId（从ZK或本地缓存）
 * 坐席→客户：AB查询 customerId → routerId（从ZK或本地缓存）
 * </pre>
 *
 * @author Customer Service Platform Team
 * @version 1.0.0
 * @see SessionStatus
 * @see SessionManager
 */
public class Session implements Serializable {

    private static final long serialVersionUID = 1L;

    /**
     * 会话唯一标识
     * <p>格式：session-{8位UUID}，如：session-a1b2c3d4</p>
     */
    private String sessionId;

    /**
     * 客户唯一标识
     * <p>格式：customer-{hash}，如：customer-12345678</p>
     */
    private String customerId;

    /**
     * 客户显示名称
     * <p>可选字段，如果未设置则显示为"访客"</p>
     */
    private String customerName;

    /**
     * 分配的坐席ID
     * <p>会话分配坐席后设置，WAITING状态时为null</p>
     */
    private String agentId;

    /**
     * 会话状态
     * @see SessionStatus
     */
    private SessionStatus status;

    /**
     * 坐席后台节点ID
     * <p>记录坐席连接的AB节点，用于消息路由。
     * 当坐席发送消息时，CF通过此字段知道发给哪个AB节点。</p>
     */
    private String backendId;

    /**
     * 客户连接的前置节点ID（路由节点ID）
     * <p>记录客户连接的CF节点，用于坐席→客户的消息路由。
     * 当坐席回复消息时，AB通过此字段知道发给哪个CF节点。</p>
     *
     * <p>此字段在以下场景使用：</p>
     * <ol>
     *   <li>客户连接CF时，CF注册 CustomerBinding: customerId → routerId</li>
     *   <li>会话分配时，SESSION_ASSIGN消息携带routerId同步给AB</li>
     *   <li>坐席回复时，AB查询routerId路由消息到正确的CF</li>
     * </ol>
     */
    private String routerId;

    /**
     * 会话创建时间（毫秒时间戳）
     */
    private long createTime;

    /**
     * 会话最后更新时间（毫秒时间戳）
     * <p>每次状态变更、消息收发都会更新</p>
     */
    private long updateTime;

    /**
     * 默认构造函数
     * <p>自动设置创建时间、更新时间，状态默认为WAITING</p>
     */
    public Session() {
        this.createTime = System.currentTimeMillis();
        this.updateTime = this.createTime;
        this.status = SessionStatus.WAITING;
    }

    /**
     * 便捷构造函数
     *
     * @param sessionId  会话ID
     * @param customerId 客户ID
     */
    public Session(String sessionId, String customerId) {
        this();
        this.sessionId = sessionId;
        this.customerId = customerId;
    }

    // ==================== Getters & Setters ====================

    public String getSessionId() {
        return sessionId;
    }

    public void setSessionId(String sessionId) {
        this.sessionId = sessionId;
    }

    public String getCustomerId() {
        return customerId;
    }

    public void setCustomerId(String customerId) {
        this.customerId = customerId;
    }

    public String getCustomerName() {
        return customerName;
    }

    public void setCustomerName(String customerName) {
        this.customerName = customerName;
    }

    public String getAgentId() {
        return agentId;
    }

    public void setAgentId(String agentId) {
        this.agentId = agentId;
    }

    public SessionStatus getStatus() {
        return status;
    }

    /**
     * 设置会话状态
     * <p>同时更新修改时间</p>
     *
     * @param status 新状态
     */
    public void setStatus(SessionStatus status) {
        this.status = status;
        this.updateTime = System.currentTimeMillis();
    }

    public String getBackendId() {
        return backendId;
    }

    public void setBackendId(String backendId) {
        this.backendId = backendId;
    }

    public String getRouterId() {
        return routerId;
    }

    public void setRouterId(String routerId) {
        this.routerId = routerId;
    }

    public long getCreateTime() {
        return createTime;
    }

    public void setCreateTime(long createTime) {
        this.createTime = createTime;
    }

    public long getUpdateTime() {
        return updateTime;
    }

    public void setUpdateTime(long updateTime) {
        this.updateTime = updateTime;
    }

    /**
     * 更新修改时间
     * <p>在状态不变但需要更新活动时间时使用</p>
     */
    public void touch() {
        this.updateTime = System.currentTimeMillis();
    }

    @Override
    public String toString() {
        return "Session{" +
                "sessionId='" + sessionId + '\'' +
                ", customerId='" + customerId + '\'' +
                ", customerName='" + customerName + '\'' +
                ", agentId='" + agentId + '\'' +
                ", status=" + status +
                ", backendId='" + backendId + '\'' +
                ", routerId='" + routerId + '\'' +
                ", createTime=" + createTime +
                ", updateTime=" + updateTime +
                '}';
    }
}
