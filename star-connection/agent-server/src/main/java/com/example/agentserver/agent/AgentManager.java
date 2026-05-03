package com.example.agentserver.agent;

import com.example.agentserver.config.AgentServerProperties;
import com.example.agentserver.config.WebSocketProperties;
import com.example.agentserver.netty.handler.ClientMessageHandler;
import com.example.agentserver.websocket.AgentWebSocketHandler;
import com.example.agentserver.zookeeper.CustomerBindingQuery;
import com.example.agentserver.session.SessionStore;
import com.example.common.model.*;
import com.example.common.util.MessageIdGenerator;
import com.fasterxml.jackson.core.JsonProcessingException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Lazy;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 坐席管理器
 *
 * <p>这是AB（坐席后台）端的核心组件，负责坐席的注册、状态管理和消息路由。</p>
 *
 * <h3>核心职责：</h3>
 * <ul>
 *   <li><b>坐席注册</b>：坐席上线时注册到ZK，通知CF节点</li>
 *   <li><b>状态管理</b>：维护坐席在线状态、会话数</li>
 *   <li><b>消息路由</b>：将坐席消息路由到正确的CF节点</li>
 * </ul>
 *
 * <h3>消息路由流程（坐席→客户）：</h3>
 * <pre>
 * ┌────────────┐     ┌────────────┐     ┌────────────┐
 * │   坐席     │────▶│  AB节点   │────▶│  CF节点   │
 * │ WebSocket  │     │AgentManager│     │            │
 * └────────────┘     └────────────┘     └────────────┘
 *                           │
 *                           │ 1. 从消息获取 sessionId
 *                           │ 2. 查询 session.routerId (SessionStore或CustomerBindingQuery)
 *                           │ 3. 获取 CF节点的 Netty连接
 *                           │ 4. 发送 CHAT_MESSAGE 消息到具体 routerId
 *                           ▼
 *                    ┌────────────┐
 *                    │ MultiRouter│
 *                    │ Connection │
 *                    │  Manager   │
 *                    └────────────┘
 * </pre>
 *
 * <h3>关键修复：</h3>
 * <p>之前坐席→客户的消息路由使用轮询，导致消息可能发送到错误的CF节点。
 * 现在通过查询routerId，精确定位客户连接的CF节点。</p>
 *
 * <h3>routerId查询策略：</h3>
 * <ol>
 *   <li>优先从本地SessionStore获取（SESSION_ASSIGN消息携带）</li>
 *   <li>其次从CustomerBindingQuery查询（ZK + 本地缓存）</li>
 * </ol>
 *
 * @author Customer Service Platform Team
 * @version 1.0.0
 * @see CustomerBindingQuery
 * @see SessionStore
 */
@Component
public class AgentManager {
    private static final Logger LOGGER = LoggerFactory.getLogger(AgentManager.class);

    private final AgentServerProperties clientProperties;
    private final WebSocketProperties webSocketProperties;
    private final ClientMessageHandler clientMessageHandler;
    private final AgentWebSocketHandler agentWebSocketHandler;
    private final AgentSessionRegistry sessionRegistry;
    private CustomerBindingQuery customerBindingQuery;
    private SessionStore sessionStore;

    // 坐席ID -> 坐席信息
    private final Map<String, AgentInfo> agents = new ConcurrentHashMap<>();

    @Autowired
    public AgentManager(AgentServerProperties clientProperties,
                        WebSocketProperties webSocketProperties,
                        @Lazy ClientMessageHandler clientMessageHandler,
                        @Lazy AgentWebSocketHandler agentWebSocketHandler,
                        AgentSessionRegistry sessionRegistry) {
        this.clientProperties = clientProperties;
        this.webSocketProperties = webSocketProperties;
        this.clientMessageHandler = clientMessageHandler;
        this.agentWebSocketHandler = agentWebSocketHandler;
        this.sessionRegistry = sessionRegistry;
    }

    @Autowired(required = false)
    public void setCustomerBindingQuery(CustomerBindingQuery customerBindingQuery) {
        this.customerBindingQuery = customerBindingQuery;
    }

    @Autowired(required = false)
    public void setSessionStore(SessionStore sessionStore) {
        this.sessionStore = sessionStore;
    }

    /**
     * 注册坐席
     */
    public void registerAgent(String agentId, String agentName) {
        AgentInfo agentInfo = new AgentInfo();
        agentInfo.setAgentId(agentId);
        agentInfo.setAgentName(agentName);
        agentInfo.setStatus(AgentStatus.ONLINE);
        agentInfo.setMaxSessions(webSocketProperties.getAgent().getMaxConcurrentSessions());
        agentInfo.setOnlineTime(System.currentTimeMillis());

        agents.put(agentId, agentInfo);

        // 向前置发送坐席注册消息
        Agent agent = new Agent(agentId, agentName);
        agent.setStatus(AgentStatus.ONLINE);
        agent.setMaxSessions(webSocketProperties.getAgent().getMaxConcurrentSessions());
        agent.setBackendId(clientProperties.getServiceId());

        Message registerMessage = new Message(MessageType.AGENT_REGISTER,
                clientProperties.getServiceId(), "router");
        registerMessage.setMessageId(MessageIdGenerator.generate());
        registerMessage.addHeader("agentId", agentId);
        try {
            registerMessage.setPayloadFromObject(agent);
        } catch (JsonProcessingException e) {
            LOGGER.error("序列化坐席信息失败", e);
            return;
        }

        clientMessageHandler.sendRequest(registerMessage);
        LOGGER.info("坐席注册: agentId={}, agentName={}", agentId, agentName);
    }

    /**
     * 注销坐席
     */
    public void unregisterAgent(String agentId) {
        AgentInfo agentInfo = agents.remove(agentId);
        if (agentInfo != null) {
            // 更新状态
            agentInfo.setStatus(AgentStatus.OFFLINE);

            // 向前置发送状态更新
            Message statusMessage = new Message(MessageType.AGENT_STATUS,
                    clientProperties.getServiceId(), "router");
            statusMessage.setMessageId(MessageIdGenerator.generate());
            statusMessage.addHeader("agentId", agentId);
            statusMessage.addHeader("status", String.valueOf(AgentStatus.OFFLINE.getCode()));

            clientMessageHandler.sendRequest(statusMessage);

            // 清除会话
            sessionRegistry.clearAgentSessions(agentId);

            LOGGER.info("坐席注销: agentId={}", agentId);
        }
    }

    /**
     * 更新坐席状态
     */
    public void updateAgentStatus(String agentId, AgentStatus status) {
        AgentInfo agentInfo = agents.get(agentId);
        if (agentInfo != null) {
            agentInfo.setStatus(status);

            // 向前置发送状态更新
            Message statusMessage = new Message(MessageType.AGENT_STATUS,
                    clientProperties.getServiceId(), "router");
            statusMessage.setMessageId(MessageIdGenerator.generate());
            statusMessage.addHeader("agentId", agentId);
            statusMessage.addHeader("status", String.valueOf(status.getCode()));

            clientMessageHandler.sendRequest(statusMessage);

            LOGGER.info("坐席状态更新: agentId={}, status={}", agentId, status);
        }
    }

    /**
     * 发送聊天消息到前置
     */
    public void sendChatMessageToRouter(String agentId, ChatMessage chatMessage, String sessionId) {
        // 查找客户连接的路由节点
        String routerId = findRouterId(sessionId);

        if (routerId == null) {
            LOGGER.warn("无法找到会话对应的路由节点: sessionId={}", sessionId);
            // 使用默认路由（轮询）
            routerId = "router";
        }

        Message message = new Message(MessageType.CHAT_MESSAGE,
                clientProperties.getServiceId(), routerId);  // 使用具体的 routerId
        message.setMessageId(MessageIdGenerator.generate());
        message.addHeader("sessionId", sessionId);
        message.addHeader("agentId", agentId);

        try {
            message.setPayloadFromObject(chatMessage);
        } catch (JsonProcessingException e) {
            LOGGER.error("序列化聊天消息失败", e);
            return;
        }

        clientMessageHandler.sendRequest(message);
        LOGGER.debug("聊天消息已发送到路由节点 {}: sessionId={}", routerId, sessionId);
    }

    /**
     * 查找会话对应的路由节点ID
     * 优先从本地 SessionStore 获取 routerId
     * 其次从 CustomerBindingQuery 查询客户绑定的路由节点
     */
    private String findRouterId(String sessionId) {
        // 1. 从本地 SessionStore 获取 routerId
        if (sessionStore != null) {
            Session session = sessionStore.findById(sessionId).orElse(null);
            if (session != null && session.getRouterId() != null) {
                LOGGER.debug("从本地会话获取路由节点: sessionId={}, routerId={}", sessionId, session.getRouterId());
                return session.getRouterId();
            }
        }

        // 2. 通过 CustomerBindingQuery 查询客户绑定的路由节点
        if (customerBindingQuery != null) {
            return customerBindingQuery.getRouterIdBySessionId(sessionId).orElse(null);
        }

        return null;
    }

    /**
     * 增加会话数
     */
    public void incrementSessionCount(String agentId) {
        AgentInfo agentInfo = agents.get(agentId);
        if (agentInfo != null) {
            agentInfo.incrementSessionCount();
        }
    }

    /**
     * 减少会话数
     */
    public void decrementSessionCount(String agentId) {
        AgentInfo agentInfo = agents.get(agentId);
        if (agentInfo != null) {
            agentInfo.decrementSessionCount();
        }
    }

    /**
     * 获取坐席名称
     */
    public String getAgentName(String agentId) {
        AgentInfo agentInfo = agents.get(agentId);
        return agentInfo != null ? agentInfo.getAgentName() : null;
    }

    /**
     * 获取坐席信息
     */
    public AgentInfo getAgentInfo(String agentId) {
        return agents.get(agentId);
    }

    /**
     * 检查坐席是否在线
     */
    public boolean isAgentOnline(String agentId) {
        AgentInfo agentInfo = agents.get(agentId);
        return agentInfo != null && agentInfo.getStatus() != AgentStatus.OFFLINE;
    }

    /**
     * 坐席信息内部类
     */
    public static class AgentInfo {
        private String agentId;
        private String agentName;
        private AgentStatus status;
        private int maxSessions = 10;
        private int currentSessions = 0;
        private long onlineTime;

        public String getAgentId() {
            return agentId;
        }

        public void setAgentId(String agentId) {
            this.agentId = agentId;
        }

        public String getAgentName() {
            return agentName;
        }

        public void setAgentName(String agentName) {
            this.agentName = agentName;
        }

        public AgentStatus getStatus() {
            return status;
        }

        public void setStatus(AgentStatus status) {
            this.status = status;
        }

        public int getMaxSessions() {
            return maxSessions;
        }

        public void setMaxSessions(int maxSessions) {
            this.maxSessions = maxSessions;
        }

        public int getCurrentSessions() {
            return currentSessions;
        }

        public void setCurrentSessions(int currentSessions) {
            this.currentSessions = currentSessions;
        }

        public long getOnlineTime() {
            return onlineTime;
        }

        public void setOnlineTime(long onlineTime) {
            this.onlineTime = onlineTime;
        }

        public synchronized void incrementSessionCount() {
            this.currentSessions++;
        }

        public synchronized void decrementSessionCount() {
            if (this.currentSessions > 0) {
                this.currentSessions--;
            }
        }
    }
}
