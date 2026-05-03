package com.example.customerserver.websocket;

import com.example.common.model.Agent;
import com.example.common.model.ChatMessage;
import com.example.common.model.SenderType;
import com.example.common.model.Session;
import com.example.common.model.SessionStatus;
import com.example.customerserver.agent.AgentRegistry;
import com.example.customerserver.message.CustomerMessageStore;
import com.example.customerserver.session.SessionManager;
import com.example.customerserver.zookeeper.CustomerBindingRegistry;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.TextWebSocketHandler;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 客户 WebSocket 处理器
 * 处理客户连接、消息接收和推送
 * 同时支持 WebSocket 实时推送和 HTTP 长轮询方式
 */
@Component
public class CustomerWebSocketHandler extends TextWebSocketHandler {
    private static final Logger LOGGER = LoggerFactory.getLogger(CustomerWebSocketHandler.class);

    private final SessionManager sessionManager;
    private final ObjectMapper objectMapper;
    private final AgentRegistry agentRegistry;
    private final CustomerMessageStore messageStore;
    private final CustomerBindingRegistry customerBindingRegistry;

    // WebSocket 会话映射：sessionId -> WebSocketSession
    private final Map<String, WebSocketSession> customerSessions = new ConcurrentHashMap<>();
    // WebSocket ID -> 业务 sessionId
    private final Map<String, String> webSocketToSession = new ConcurrentHashMap<>();

    @Autowired
    public CustomerWebSocketHandler(SessionManager sessionManager,
                                    ObjectMapper objectMapper,
                                    AgentRegistry agentRegistry,
                                    CustomerMessageStore messageStore,
                                    CustomerBindingRegistry customerBindingRegistry) {
        this.sessionManager = sessionManager;
        this.objectMapper = objectMapper;
        this.agentRegistry = agentRegistry;
        this.messageStore = messageStore;
        this.customerBindingRegistry = customerBindingRegistry;
    }

    @Override
    public void afterConnectionEstablished(WebSocketSession session) throws Exception {
        String sessionId = extractSessionId(session);
        String wsId = session.getId();

        InetSocketAddress remoteAddress = session.getRemoteAddress();

        LOGGER.info("客户 WebSocket 连接建立: wsId={}, sessionId={}, remoteAddress={}",
                wsId, sessionId, remoteAddress);

        // 检查是否提供了有效的 sessionId
        if (sessionId != null && !sessionId.isEmpty()) {
            // 尝试使用现有会话
            Optional<Session> existingSession = sessionManager.getSession(sessionId);
            if (existingSession.isPresent()) {
                Session s = existingSession.get();
                if (s.getStatus() != SessionStatus.CLOSED) {
                    // 恢复现有会话
                    customerSessions.put(s.getSessionId(), session);
                    webSocketToSession.put(wsId, s.getSessionId());

                    LOGGER.info("客户 WebSocket 恢复现有会话: sessionId={}, status={}, agentId={}",
                            s.getSessionId(), s.getStatus(), s.getAgentId());

                    // 发送会话状态
                    sendMessage(session, createSessionStatusMessage(s));

                    // 如果会话已有坐席，发送 SESSION_ASSIGN 消息
                    if (s.getStatus() == SessionStatus.ACTIVE && s.getAgentId() != null) {
                        pushSessionAssign(s.getSessionId(), s.getAgentId(), null);
                    }
                    return;
                }
            } else {
                LOGGER.warn("提供的 sessionId {} 不存在，将创建新会话", sessionId);
            }
        }

        // 创建新会话
        String customerId = "customer-" + (remoteAddress != null ?
                remoteAddress.toString().hashCode() : UUID.randomUUID().toString().substring(0, 8));

        Session newSession = sessionManager.createSession(new com.example.customerserver.dto.CustomerInfo(customerId, null));
        customerSessions.put(newSession.getSessionId(), session);
        webSocketToSession.put(wsId, newSession.getSessionId());

        // 注册客户绑定关系到 ZooKeeper
        String routerId = customerBindingRegistry.getCurrentRouterId();
        customerBindingRegistry.registerBinding(customerId, routerId);
        LOGGER.info("客户绑定已注册: customerId={}, routerId={}", customerId, routerId);

        LOGGER.info("客户 WebSocket 创建新会话: sessionId={}", newSession.getSessionId());

        // 发送会话创建成功消息
        sendMessage(session, createSessionCreatedMessage(newSession));
    }

    /**
     * 通过 sessionId 连接已有的 WebSocket 会话（用于 REST API 创建会话后连接）
     */
    public void connectSession(String sessionId, WebSocketSession session) {
        customerSessions.put(sessionId, session);
        webSocketToSession.put(session.getId(), sessionId);

        Optional<Session> optionalSession = sessionManager.getSession(sessionId);
        if (optionalSession.isPresent()) {
            Session s = optionalSession.get();
            sendMessage(session, createSessionStatusMessage(s));
        }
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) throws Exception {
        String payload = message.getPayload();
        String sessionId = webSocketToSession.get(session.getId());

        if (sessionId == null) {
            LOGGER.warn("收到消息但找不到对应的会话: wsId={}", session.getId());
            sendMessage(session, createErrorMessage("会话未找到"));
            return;
        }

        LOGGER.debug("收到客户消息: sessionId={}, payload={}", sessionId, payload);

        try {
            // 解析消息
            Map<String, Object> msgMap = objectMapper.readValue(payload, Map.class);
            String type = (String) msgMap.get("type");

            // 处理心跳
            if ("PING".equals(type)) {
                Map<String, Object> pong = new java.util.HashMap<>();
                pong.put("type", "PONG");
                pong.put("timestamp", System.currentTimeMillis());
                sendMessage(session, pong);
                return;
            }

            // 处理聊天消息
            if ("CHAT_MESSAGE".equals(type) || "chat".equals(type)) {
                String content = (String) msgMap.get("content");

                // 检查会话状态
                Optional<Session> optionalSession = sessionManager.getSession(sessionId);
                if (optionalSession.isEmpty() || optionalSession.get().getStatus() == SessionStatus.CLOSED) {
                    sendMessage(session, createErrorMessage("会话已关闭"));
                    return;
                }

                // 创建聊天消息
                ChatMessage chatMessage = new ChatMessage();
                chatMessage.setSessionId(sessionId);
                chatMessage.setSenderType(SenderType.CUSTOMER);
                chatMessage.setSenderId((String) msgMap.getOrDefault("senderId", "customer"));
                chatMessage.setSenderName((String) msgMap.getOrDefault("senderName", "客户"));
                chatMessage.setContent(content);
                chatMessage.setTimestamp(System.currentTimeMillis());

                // 路由消息到坐席
                sessionManager.routeMessage(sessionId, chatMessage);
            }

        } catch (Exception e) {
            LOGGER.error("处理客户消息失败: sessionId={}", sessionId, e);
            sendMessage(session, createErrorMessage("消息处理失败: " + e.getMessage()));
        }
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) throws Exception {
        String sessionId = webSocketToSession.remove(session.getId());
        customerSessions.remove(sessionId);

        LOGGER.info("客户 WebSocket 连接关闭: wsId={}, sessionId={}, status={}",
                session.getId(), sessionId, status);

        if (sessionId != null) {
            // 获取客户ID并注销绑定
            Optional<Session> optionalSession = sessionManager.getSession(sessionId);
            if (optionalSession.isPresent()) {
                String customerId = optionalSession.get().getCustomerId();
                if (customerId != null) {
                    customerBindingRegistry.unregisterBinding(customerId);
                    LOGGER.info("客户绑定已注销: customerId={}", customerId);
                }
            }
            sessionManager.removeCustomerChannel(sessionId);
        }
    }

    @Override
    public void handleTransportError(WebSocketSession session, Throwable exception) throws Exception {
        LOGGER.error("客户 WebSocket 传输错误: wsId={}", session.getId(), exception);
    }

    /**
     * 推送会话分配通知给客户
     */
    public void pushSessionAssign(String sessionId, String agentId, String agentName) {
        WebSocketSession session = customerSessions.get(sessionId);
        if (session == null || !session.isOpen()) {
            LOGGER.warn("客户 WebSocket 会话不存在或已关闭: sessionId={}", sessionId);
            return;
        }

        // 如果未提供坐席名称，从注册表获取
        if (agentName == null && agentId != null) {
            Optional<Agent> agentOpt = agentRegistry.findById(agentId);
            if (agentOpt.isPresent()) {
                agentName = agentOpt.get().getAgentName();
            }
        }
        if (agentName == null) {
            agentName = "客服";
        }

        Map<String, Object> msg = new java.util.HashMap<>();
        msg.put("type", "SESSION_ASSIGN");
        msg.put("sessionId", sessionId);
        msg.put("agentId", agentId);
        msg.put("agentName", agentName);
        msg.put("timestamp", System.currentTimeMillis());
        sendMessage(session, msg);
        LOGGER.info("已推送会话分配通知给客户: sessionId={}, agentId={}, agentName={}", sessionId, agentId, agentName);
    }

    /**
     * 推送会话状态更新给客户
     */
    public void pushSessionStatus(String sessionId, Session s) {
        WebSocketSession session = customerSessions.get(sessionId);
        if (session == null || !session.isOpen()) {
            return;
        }
        sendMessage(session, createSessionStatusMessage(s));
    }

    /**
     * 推送消息给客户
     * 同时支持 WebSocket 和 HTTP 长轮询
     */
    public void pushToCustomer(String sessionId, ChatMessage message) {
        // 1. 存储消息到队列，供 HTTP 长轮询使用
        messageStore.addMessage(sessionId, message);
        LOGGER.debug("消息已存储到队列: sessionId={}, messageId={}", sessionId, message.getMessageId());

        // 2. 如果客户通过 WebSocket 在线，实时推送
        WebSocketSession session = customerSessions.get(sessionId);
        if (session != null && session.isOpen()) {
            try {
                Map<String, Object> msg = new java.util.HashMap<>();
                msg.put("type", "CHAT_MESSAGE");
                msg.put("sessionId", message.getSessionId());
                msg.put("senderType", message.getSenderType().name());
                msg.put("senderId", message.getSenderId());
                msg.put("senderName", message.getSenderName());
                msg.put("content", message.getContent());
                msg.put("timestamp", message.getTimestamp());

                String json = objectMapper.writeValueAsString(msg);
                session.sendMessage(new TextMessage(json));
                LOGGER.debug("消息已通过 WebSocket 推送给客户: sessionId={}", sessionId);
            } catch (IOException e) {
                LOGGER.error("WebSocket 推送消息失败: sessionId={}", sessionId, e);
            }
        } else {
            LOGGER.debug("客户 WebSocket 不在线，消息已存入队列等待 HTTP 长轮询获取: sessionId={}", sessionId);
        }
    }

    /**
     * 发送系统消息给客户
     */
    public void sendSystemMessage(String sessionId, String content) {
        ChatMessage message = ChatMessage.systemMessage(sessionId, content);
        pushToCustomer(sessionId, message);
    }

    /**
     * 检查客户是否在线
     */
    public boolean isCustomerOnline(String sessionId) {
        WebSocketSession session = customerSessions.get(sessionId);
        return session != null && session.isOpen();
    }

    /**
     * 获取客户 WebSocket 连接数
     */
    public int getConnectionCount() {
        return (int) customerSessions.values().stream().filter(WebSocketSession::isOpen).count();
    }

    /**
     * 获取所有客户 WebSocket 会话 ID
     */
    public java.util.Set<String> getConnectedSessionIds() {
        return customerSessions.entrySet().stream()
                .filter(e -> e.getValue().isOpen())
                .map(Map.Entry::getKey)
                .collect(java.util.stream.Collectors.toSet());
    }

    private void sendMessage(WebSocketSession session, Object message) {
        try {
            String json = objectMapper.writeValueAsString(message);
            session.sendMessage(new TextMessage(json));
        } catch (IOException e) {
            LOGGER.error("发送消息失败: wsId={}", session.getId(), e);
        }
    }

    private String extractSessionId(WebSocketSession session) {
        // 从 URL 路径中提取 sessionId: /ws/customer/{sessionId}
        String path = session.getUri().getPath();
        String[] parts = path.split("/");
        if (parts.length > 0) {
            String lastPart = parts[parts.length - 1];
            // 如果最后一部分是 sessionId 格式，返回它
            if (lastPart.startsWith("session-") || lastPart.length() > 10) {
                return lastPart;
            }
        }

        // 也支持 URL 参数方式
        String query = session.getUri().getQuery();
        if (query != null) {
            for (String param : query.split("&")) {
                if (param.startsWith("sessionId=")) {
                    return param.substring("sessionId=".length());
                }
            }
        }
        return null;
    }

    private String generateSessionId() {
        return "session-" + UUID.randomUUID().toString().substring(0, 8);
    }

    private Object createSessionCreatedMessage(Session session) {
        Map<String, Object> msg = new java.util.HashMap<>();
        msg.put("type", "session_created");
        msg.put("sessionId", session.getSessionId());
        msg.put("customerId", session.getCustomerId());
        msg.put("status", session.getStatus().name());
        return msg;
    }

    private Object createSessionStatusMessage(Session session) {
        Map<String, Object> msg = new java.util.HashMap<>();
        msg.put("type", "session_status");
        msg.put("sessionId", session.getSessionId());
        msg.put("customerId", session.getCustomerId());
        msg.put("agentId", session.getAgentId());
        msg.put("status", session.getStatus().name());
        return msg;
    }

    private Object createErrorMessage(String error) {
        Map<String, Object> msg = new java.util.HashMap<>();
        msg.put("type", "error");
        msg.put("message", error);
        return msg;
    }
}
