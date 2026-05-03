package com.example.customerserver.session;

import com.example.common.model.Agent;
import com.example.common.model.ChatMessage;
import com.example.common.model.Message;
import com.example.common.model.MessageType;
import com.example.common.model.Session;
import com.example.common.model.SessionStatus;
import com.example.common.util.MessageIdGenerator;
import com.example.customerserver.agent.AgentLoadBalancer;
import com.example.customerserver.agent.AgentRegistry;
import com.example.customerserver.dto.CustomerInfo;
import com.example.customerserver.netty.manager.ConnectionManager;
import com.example.customerserver.websocket.CustomerWebSocketHandler;
import com.example.customerserver.config.CustomerServerProperties;
import com.fasterxml.jackson.core.JsonProcessingException;
import io.netty.channel.Channel;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Lazy;
import org.springframework.stereotype.Component;

import jakarta.annotation.PostConstruct;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

/**
 * 会话管理器
 *
 * <p>这是CF（客户前置）端的核心组件，负责会话的完整生命周期管理，
 * 包括创建、分配坐席、消息路由、状态转换和关闭。</p>
 *
 * <h3>核心职责：</h3>
 * <ul>
 *   <li><b>会话创建</b>：客户进线时创建会话，设置routerId</li>
 *   <li><b>坐席分配</b>：使用负载均衡策略选择最优坐席</li>
 *   <li><b>消息路由</b>：将客户消息路由到正确的AB节点</li>
 *   <li><b>状态管理</b>：使用状态机管理会话状态转换</li>
 *   <li><b>会话关闭</b>：处理各种关闭场景（客户断开、超时、手动关闭）</li>
 * </ul>
 *
 * <h3>消息路由流程（客户→坐席）：</h3>
 * <pre>
 * ┌────────────┐     ┌────────────┐     ┌────────────┐
 * │   客户     │────▶│  CF节点   │────▶│  AB节点   │
 * │ WebSocket  │     │SessionMgr │     │            │
 * └────────────┘     └────────────┘     └────────────┘
 *                           │
 *                           │ 1. 查询 session.agentId
 *                           │ 2. 查询 agentId → backendId (ZK/本地缓存)
 *                           │ 3. 获取 AB节点的 Netty连接
 *                           │ 4. 发送 CHAT_MESSAGE 消息
 *                           ▼
 *                    ┌────────────┐
 *                    │ Connection │
 *                    │  Manager   │
 *                    └────────────┘
 * </pre>
 *
 * <h3>会话状态机：</h3>
 * <pre>
 *                    ┌───────────┐
 *                    │  CREATE   │
 *                    └─────┬─────┘
 *                          │
 *                          ▼
 *     ┌──────────┐    ┌──────────┐    ┌──────────┐
 *     │ WAITING  │───▶│  ACTIVE  │───▶│  CLOSED  │
 *     │(等待坐席)│    │(对话中) │    │ (已关闭) │
 *     └──────────┘    └──────────┘    └──────────┘
 *         │               │               ▲
 *         │               │               │
 *         └───────────────┴───────────────┘
 *                 (超时/断开)
 * </pre>
 *
 * <h3>与其他组件的协作：</h3>
 * <ul>
 *   <li>{@link SessionStore} - 会话持久化存储</li>
 *   <li>{@link AgentRegistry} - 坐席注册表，查询可用坐席</li>
 *   <li>{@link AgentLoadBalancer} - 坐席负载均衡（最少连接数优先）</li>
 *   <li>{@link ConnectionManager} - AB节点连接管理</li>
 *   <li>{@link CustomerWebSocketHandler} - 客户WebSocket处理</li>
 *   <li>{@link SessionStateTransitionManager} - 状态转换管理</li>
 * </ul>
 *
 * @author Customer Service Platform Team
 * @version 1.0.0
 * @see Session
 * @see SessionStateTransitionManager
 * @see AgentLoadBalancer
 */
@Component
public class SessionManager implements SessionStateListener {
    private static final Logger LOGGER = LoggerFactory.getLogger(SessionManager.class);

    private final SessionStore sessionStore;
    private final AgentRegistry agentRegistry;
    private final AgentLoadBalancer loadBalancer;
    private final ConnectionManager connectionManager;
    private final SessionStateTransitionManager transitionManager;
    private final CustomerServerProperties routerProperties;
    private CustomerWebSocketHandler customerWebSocketHandler;

    @Autowired
    public SessionManager(SessionStore sessionStore,
                          AgentRegistry agentRegistry,
                          AgentLoadBalancer loadBalancer,
                          ConnectionManager connectionManager,
                          SessionStateTransitionManager transitionManager,
                          CustomerServerProperties routerProperties) {
        this.sessionStore = sessionStore;
        this.agentRegistry = agentRegistry;
        this.loadBalancer = loadBalancer;
        this.connectionManager = connectionManager;
        this.transitionManager = transitionManager;
        this.routerProperties = routerProperties;
    }

    @Autowired(required = false)
    @Lazy
    public void setCustomerWebSocketHandler(CustomerWebSocketHandler customerWebSocketHandler) {
        this.customerWebSocketHandler = customerWebSocketHandler;
        LOGGER.info("CustomerWebSocketHandler 已注入到 SessionManager");
    }

    @PostConstruct
    public void init() {
        // 注册自身为状态监听器
        transitionManager.addListener(this);
        LOGGER.info("会话管理器已初始化，已注册状态监听器");
    }

    /**
     * 创建新会话
     */
    public Session createSession(CustomerInfo customerInfo) {
        // 检查客户是否已有活跃会话
        Optional<Session> existingSession = sessionStore.findByCustomerId(customerInfo.getCustomerId());
        if (existingSession.isPresent() && existingSession.get().getStatus() != SessionStatus.CLOSED) {
            LOGGER.info("客户 {} 已有活跃会话: {}", customerInfo.getCustomerId(), existingSession.get().getSessionId());
            return existingSession.get();
        }

        // 创建新会话
        String sessionId = generateSessionId();
        Session session = new Session(sessionId, customerInfo.getCustomerId());
        session.setCustomerName(customerInfo.getCustomerName());

        // 保存路由节点ID
        session.setRouterId(routerProperties.getServiceId());

        // 保存会话
        sessionStore.save(session);
        LOGGER.info("创建新会话: {}, 客户: {}, 路由节点: {}",
                sessionId, customerInfo.getCustomerId(), session.getRouterId());

        // 触发创建事件
        transitionManager.transition(session, SessionEvent.CREATE);

        // 尝试分配坐席
        tryAssignAgent(session);

        return session;
    }

    /**
     * 尝试为会话分配坐席
     */
    private boolean tryAssignAgent(Session session) {
        Agent agent = loadBalancer.selectAgent();
        if (agent == null) {
            LOGGER.warn("没有可用的坐席，会话 {} 进入等待状态", session.getSessionId());
            return false;
        }

        return assignAgentToSession(session, agent);
    }

    /**
     * 将坐席分配给会话
     */
    private boolean assignAgentToSession(Session session, Agent agent) {
        // 执行状态转换
        SessionTransitionResult result = transitionManager.transition(session, SessionEvent.ASSIGN_AGENT, agent, null);

        if (!result.isSuccess()) {
            LOGGER.warn("会话 {} 分配坐席失败，状态转换无效: {}", session.getSessionId(), result.getErrorMessage());
            return false;
        }

        // 更新会话信息
        session.setAgentId(agent.getAgentId());
        session.setBackendId(agent.getBackendId());
        sessionStore.save(session);

        // 更新坐席会话数
        agent.incrementSessions();

        // 通知坐席后台
        notifyAgentSessionAssigned(session, agent);

        // 通知客户（通过 WebSocket）
        notifyCustomerSessionAssigned(session, agent);

        LOGGER.info("会话 {} 分配给坐席 {} (后台节点 {})",
                session.getSessionId(), agent.getAgentId(), agent.getBackendId());
        return true;
    }

    /**
     * 为会话分配坐席（外部调用）
     */
    public boolean assignAgent(Session session) {
        if (session.getStatus() != SessionStatus.WAITING) {
            LOGGER.warn("会话 {} 状态不是等待中，无法分配坐席: {}", session.getSessionId(), session.getStatus());
            return false;
        }
        return tryAssignAgent(session);
    }

    /**
     * 转接会话到其他坐席
     */
    public boolean transferSession(String sessionId, String targetAgentId) {
        Optional<Session> optionalSession = sessionStore.findById(sessionId);
        if (optionalSession.isEmpty()) {
            LOGGER.warn("会话不存在: {}", sessionId);
            return false;
        }

        Session session = optionalSession.get();
        if (session.getStatus() != SessionStatus.ACTIVE) {
            LOGGER.warn("会话 {} 状态不是活跃状态，无法转接: {}", sessionId, session.getStatus());
            return false;
        }

        Optional<Agent> optionalAgent = agentRegistry.findById(targetAgentId);
        if (optionalAgent.isEmpty()) {
            LOGGER.warn("目标坐席不存在: {}", targetAgentId);
            return false;
        }

        Agent newAgent = optionalAgent.get();
        if (!newAgent.canAcceptSession()) {
            LOGGER.warn("目标坐席 {} 无法接受新会话", targetAgentId);
            return false;
        }

        // 释放原坐席的会话数
        if (session.getAgentId() != null) {
            agentRegistry.findById(session.getAgentId()).ifPresent(Agent::decrementSessions);
        }

        // 执行状态转换（转接事件）
        transitionManager.transition(session, SessionEvent.TRANSFER, newAgent, null);

        // 更新会话信息
        String oldAgentId = session.getAgentId();
        session.setAgentId(newAgent.getAgentId());
        session.setBackendId(newAgent.getBackendId());
        sessionStore.save(session);

        // 更新新坐席会话数
        newAgent.incrementSessions();

        // 通知新坐席后台
        notifyAgentSessionAssigned(session, newAgent);

        LOGGER.info("会话 {} 从坐席 {} 转接到坐席 {}",
                sessionId, oldAgentId, newAgent.getAgentId());
        return true;
    }

    /**
     * 路由消息到坐席后台
     */
    public void routeMessage(String sessionId, ChatMessage chatMessage) {
        Optional<Session> optionalSession = sessionStore.findById(sessionId);
        if (optionalSession.isEmpty()) {
            LOGGER.warn("会话不存在: {}", sessionId);
            return;
        }

        Session session = optionalSession.get();

        // 检查会话状态
        if (session.getStatus() == SessionStatus.CLOSED) {
            LOGGER.warn("会话 {} 已关闭，无法发送消息", sessionId);
            return;
        }

        // 如果会话处于等待状态，尝试激活
        if (session.getStatus() == SessionStatus.WAITING) {
            transitionManager.transition(session, SessionEvent.CUSTOMER_MESSAGE);
            sessionStore.save(session);
        }

        // 检查是否有分配的坐席
        if (session.getBackendId() == null) {
            LOGGER.warn("会话 {} 没有分配坐席后台", sessionId);
            return;
        }

        // 构建消息发送到坐席后台
        Message message = new Message(MessageType.CHAT_MESSAGE, "customer-frontend", session.getBackendId());
        message.setMessageId(MessageIdGenerator.generate());
        try {
            message.setPayloadFromObject(chatMessage);
        } catch (JsonProcessingException e) {
            LOGGER.error("序列化聊天消息失败", e);
            return;
        }
        message.addHeader("sessionId", sessionId);
        message.addHeader("agentId", session.getAgentId());

        // 发送到坐席后台
        boolean sent = connectionManager.sendMessage(session.getBackendId(), message);
        if (sent) {
            LOGGER.debug("消息从会话 {} 路由到坐席后台 {}", sessionId, session.getBackendId());
        } else {
            LOGGER.warn("发送消息到坐席后台 {} 失败", session.getBackendId());
        }
    }

    /**
     * 关闭会话
     */
    public void closeSession(String sessionId) {
        closeSession(sessionId, SessionEvent.CLOSE, null);
    }

    /**
     * 客户断开连接关闭会话
     */
    public void closeSessionByCustomerDisconnect(String sessionId) {
        closeSession(sessionId, SessionEvent.CUSTOMER_DISCONNECT, null);
    }

    /**
     * 会话超时关闭
     */
    public void closeSessionByTimeout(String sessionId) {
        closeSession(sessionId, SessionEvent.TIMEOUT, "会话超时");
    }

    /**
     * 关闭会话（内部方法）
     */
    private void closeSession(String sessionId, SessionEvent event, String reason) {
        Optional<Session> optionalSession = sessionStore.findById(sessionId);
        if (optionalSession.isEmpty()) {
            LOGGER.warn("会话不存在: {}", sessionId);
            return;
        }

        Session session = optionalSession.get();

        // 检查是否可以关闭
        if (session.getStatus() == SessionStatus.CLOSED) {
            LOGGER.debug("会话 {} 已经是关闭状态", sessionId);
            return;
        }

        // 执行状态转换
        SessionTransitionResult result = transitionManager.transition(session, event, null, reason);
        if (!result.isSuccess() && !result.isStateChanged()) {
            LOGGER.warn("会话 {} 关闭失败: {}", sessionId, result.getErrorMessage());
            return;
        }

        sessionStore.save(session);

        // 更新坐席会话数
        if (session.getAgentId() != null) {
            Optional<Agent> optionalAgent = agentRegistry.findById(session.getAgentId());
            optionalAgent.ifPresent(agent -> {
                agent.decrementSessions();
                // 通知坐席后台会话关闭
                notifyAgentSessionClosed(session, agent);
            });
        }

        LOGGER.info("会话 {} 已关闭 (原因: {})", sessionId, SessionStateMachine.getEventDisplayName(event));
    }

    /**
     * 重新分配等待中的会话
     */
    public boolean reassignWaitingSession(String sessionId) {
        Optional<Session> optionalSession = sessionStore.findById(sessionId);
        if (optionalSession.isEmpty()) {
            LOGGER.warn("会话不存在: {}", sessionId);
            return false;
        }

        Session session = optionalSession.get();
        if (session.getStatus() != SessionStatus.WAITING) {
            LOGGER.warn("会话 {} 状态不是等待中: {}", sessionId, session.getStatus());
            return false;
        }

        // 执行重新分配事件
        transitionManager.transition(session, SessionEvent.REASSIGN);

        return tryAssignAgent(session);
    }

    /**
     * 获取会话
     */
    public Optional<Session> getSession(String sessionId) {
        return sessionStore.findById(sessionId);
    }

    /**
     * 获取客户的活跃会话
     */
    public Optional<Session> getActiveSessionByCustomerId(String customerId) {
        return sessionStore.findByCustomerId(customerId)
                .filter(s -> s.getStatus() != SessionStatus.CLOSED);
    }

    /**
     * 获取等待分配的会话列表
     */
    public List<Session> getWaitingSessions() {
        return sessionStore.findByStatus(SessionStatus.WAITING);
    }

    /**
     * 获取活跃会话列表
     */
    public List<Session> getActiveSessions() {
        return sessionStore.findByStatus(SessionStatus.ACTIVE);
    }

    /**
     * 获取坐席名称
     */
    public Optional<String> getAgentName(String agentId) {
        if (agentId == null) {
            return Optional.empty();
        }
        return agentRegistry.findById(agentId)
                .map(Agent::getAgentName);
    }

    /**
     * 尝试为等待中的会话分配坐席
     */
    public void processWaitingSessions() {
        List<Session> waitingSessions = getWaitingSessions();
        for (Session session : waitingSessions) {
            tryAssignAgent(session);
        }
    }

    /**
     * 注册客户WebSocket通道
     */
    public void registerCustomerChannel(String sessionId, Channel channel) {
        Optional<Session> optionalSession = sessionStore.findById(sessionId);
        if (optionalSession.isPresent()) {
            LOGGER.debug("注册客户通道: sessionId={}", sessionId);
        }
    }

    /**
     * 移除客户WebSocket通道
     */
    public void removeCustomerChannel(String sessionId) {
        Optional<Session> optionalSession = sessionStore.findById(sessionId);
        if (optionalSession.isPresent()) {
            Session session = optionalSession.get();
            if (session.getStatus() != SessionStatus.CLOSED) {
                LOGGER.info("客户断开连接，关闭会话: {}", sessionId);
                closeSessionByCustomerDisconnect(sessionId);
            }
        }
    }

    // ========== SessionStateListener 实现 ==========

    @Override
    public void onSessionCreated(SessionLifecycleEvent event) {
        LOGGER.info("会话创建事件: {}", event.getSession().getSessionId());
    }

    @Override
    public void onSessionAssigned(SessionLifecycleEvent event) {
        LOGGER.info("会话分配事件: sessionId={}, agentId={}",
                event.getSession().getSessionId(),
                event.getAgent() != null ? event.getAgent().getAgentId() : "null");
    }

    @Override
    public void onSessionActivated(SessionLifecycleEvent event) {
        LOGGER.info("会话激活事件: {}", event.getSession().getSessionId());
    }

    @Override
    public void onSessionClosed(SessionLifecycleEvent event) {
        LOGGER.info("会话关闭事件: sessionId={}, event={}",
                event.getSession().getSessionId(), event.getEvent());
    }

    @Override
    public void onSessionTimeout(SessionLifecycleEvent event) {
        LOGGER.warn("会话超时事件: {}", event.getSession().getSessionId());
    }

    @Override
    public void onSessionTransferred(SessionLifecycleEvent event) {
        LOGGER.info("会话转接事件: sessionId={}, agentId={}",
                event.getSession().getSessionId(),
                event.getAgent() != null ? event.getAgent().getAgentId() : "null");
    }

    @Override
    public void onError(SessionLifecycleEvent event, Exception error) {
        LOGGER.error("会话事件处理错误: sessionId={}, error={}",
                event.getSession().getSessionId(), error.getMessage(), error);
    }

    // ========== 私有方法 ==========

    private void notifyAgentSessionAssigned(Session session, Agent agent) {
        Message message = new Message(MessageType.SESSION_ASSIGN, "customer-frontend", agent.getBackendId());
        message.setMessageId(MessageIdGenerator.generate());
        message.addHeader("sessionId", session.getSessionId());
        message.addHeader("agentId", agent.getAgentId());
        message.addHeader("customerId", session.getCustomerId());
        message.addHeader("customerName", session.getCustomerName() != null ? session.getCustomerName() : "");

        try {
            message.setPayloadFromObject(session);
        } catch (JsonProcessingException e) {
            LOGGER.error("序列化会话信息失败", e);
            return;
        }

        boolean sent = connectionManager.sendMessage(agent.getBackendId(), message);
        if (!sent) {
            LOGGER.warn("通知坐席后台会话分配失败: agentId={}, backendId={}", agent.getAgentId(), agent.getBackendId());
        }
    }

    /**
     * 通知客户会话已分配坐席
     */
    private void notifyCustomerSessionAssigned(Session session, Agent agent) {
        if (customerWebSocketHandler != null) {
            customerWebSocketHandler.pushSessionAssign(
                    session.getSessionId(),
                    agent.getAgentId(),
                    agent.getAgentName()
            );
        } else {
            LOGGER.warn("CustomerWebSocketHandler 未注入，无法通知客户会话分配");
        }
    }

    private void notifyAgentSessionClosed(Session session, Agent agent) {
        Message message = new Message(MessageType.SESSION_CLOSE, "customer-frontend", agent.getBackendId());
        message.setMessageId(MessageIdGenerator.generate());
        message.addHeader("sessionId", session.getSessionId());
        message.addHeader("agentId", agent.getAgentId());

        connectionManager.sendMessage(agent.getBackendId(), message);
    }

    private String generateSessionId() {
        return "session-" + UUID.randomUUID().toString().substring(0, 8);
    }
}
