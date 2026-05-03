package com.example.agentserver.websocket;

import com.example.agentserver.agent.AgentManager;
import com.example.agentserver.agent.AgentSessionRegistry;
import com.example.agentserver.netty.handler.ClientMessageHandler;
import com.example.agentserver.zookeeper.AgentBindingRegistry;
import com.example.common.model.*;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Lazy;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.TextWebSocketHandler;

import java.io.IOException;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 坐席 WebSocket 处理器
 * 处理坐席连接、消息接收和推送
 * 坐席登录后将绑定关系注册到 ZooKeeper，用于跨节点消息路由
 */
@Component
public class AgentWebSocketHandler extends TextWebSocketHandler {
    private static final Logger LOGGER = LoggerFactory.getLogger(AgentWebSocketHandler.class);

    private final AgentManager agentManager;
    private final AgentSessionRegistry sessionRegistry;
    private final ClientMessageHandler clientMessageHandler;
    private final ObjectMapper objectMapper;
    private final AgentBindingRegistry agentBindingRegistry;

    // 坐席ID -> WebSocketSession
    private final Map<String, WebSocketSession> agentSessions = new ConcurrentHashMap<>();
    // WebSocket ID -> 坐席ID
    private final Map<String, String> webSocketToAgent = new ConcurrentHashMap<>();

    @Autowired
    public AgentWebSocketHandler(@Lazy AgentManager agentManager,
                                  AgentSessionRegistry sessionRegistry,
                                  @Lazy ClientMessageHandler clientMessageHandler,
                                  ObjectMapper objectMapper,
                                  AgentBindingRegistry agentBindingRegistry) {
        this.agentManager = agentManager;
        this.sessionRegistry = sessionRegistry;
        this.clientMessageHandler = clientMessageHandler;
        this.objectMapper = objectMapper;
        this.agentBindingRegistry = agentBindingRegistry;
    }

    @Override
    public void afterConnectionEstablished(WebSocketSession session) throws Exception {
        String agentId = extractAgentId(session);
        if (agentId == null || agentId.isEmpty()) {
            LOGGER.warn("坐席连接缺少 agentId 参数");
            session.close(CloseStatus.BAD_DATA);
            return;
        }

        String agentName = extractQueryParam(session, "name", "Agent-" + agentId.substring(0, Math.min(4, agentId.length())));

        LOGGER.info("坐席 WebSocket 连接建立: wsId={}, agentId={}, agentName={}",
                session.getId(), agentId, agentName);

        // 存储会话映射
        agentSessions.put(agentId, session);
        webSocketToAgent.put(session.getId(), agentId);

        // 注册坐席到 Router
        agentManager.registerAgent(agentId, agentName);

        // 注册坐席绑定关系到 ZooKeeper（用于跨节点消息路由）
        String backendId = agentBindingRegistry.getCurrentBackendId();
        agentBindingRegistry.registerAgentBinding(agentId, backendId);
        LOGGER.info("坐席绑定关系已注册到 ZK: agentId={}, backendId={}", agentId, backendId);

        // 发送连接成功消息
        sendMessage(session, createConnectionSuccessMessage(agentId));
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) throws Exception {
        String payload = message.getPayload();
        String agentId = webSocketToAgent.get(session.getId());

        if (agentId == null) {
            LOGGER.warn("收到消息但找不到对应的坐席: wsId={}", session.getId());
            sendMessage(session, createErrorMessage("坐席未找到"));
            return;
        }

        LOGGER.debug("收到坐席消息: agentId={}, payload={}", agentId, payload);

        try {
            // 解析消息
            Map<String, Object> msgMap = objectMapper.readValue(payload, Map.class);
            String type = (String) msgMap.get("type");

            switch (type) {
                case "AGENT_REGISTER":
                    handleAgentRegister(agentId, msgMap);
                    break;
                case "CHAT_MESSAGE":
                    handleChatMessage(agentId, msgMap);
                    break;
                case "SESSION_ACCEPT":
                    handleSessionAccept(agentId, msgMap);
                    break;
                case "SESSION_CLOSE":
                    handleSessionClose(agentId, msgMap);
                    break;
                case "AGENT_STATUS":
                    handleStatusUpdate(agentId, msgMap);
                    break;
                case "PING":
                    handlePing(session);
                    break;
                // 兼容旧格式
                case "chat":
                    handleChatMessage(agentId, msgMap);
                    break;
                case "status":
                    handleStatusUpdate(agentId, msgMap);
                    break;
                case "session_accept":
                    handleSessionAccept(agentId, msgMap);
                    break;
                case "session_close":
                    handleSessionClose(agentId, msgMap);
                    break;
                default:
                    LOGGER.warn("未知的消息类型: {}", type);
            }
        } catch (Exception e) {
            LOGGER.error("处理坐席消息失败: agentId={}", agentId, e);
            sendMessage(session, createErrorMessage("消息处理失败: " + e.getMessage()));
        }
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) throws Exception {
        String agentId = webSocketToAgent.remove(session.getId());
        agentSessions.remove(agentId);

        LOGGER.info("坐席 WebSocket 连接关闭: wsId={}, agentId={}, status={}",
                session.getId(), agentId, status);

        if (agentId != null) {
            // 注销坐席
            agentManager.unregisterAgent(agentId);

            // 从 ZooKeeper 注销坐席绑定关系
            agentBindingRegistry.unregisterAgent(agentId);
            LOGGER.info("坐席绑定关系已从 ZK 注销: agentId={}", agentId);
        }
    }

    @Override
    public void handleTransportError(WebSocketSession session, Throwable exception) throws Exception {
        LOGGER.error("坐席 WebSocket 传输错误: wsId={}", session.getId(), exception);
    }

    /**
     * 处理坐席注册
     */
    private void handleAgentRegister(String agentId, Map<String, Object> msgMap) {
        String agentName = (String) msgMap.get("agentName");
        if (agentName != null && !agentName.isEmpty()) {
            agentManager.registerAgent(agentId, agentName);
            // 发送确认消息
            Map<String, Object> response = new java.util.HashMap<>();
            response.put("type", "AGENT_REGISTER_ACK");
            response.put("agentId", agentId);
            response.put("status", "ONLINE");
            response.put("timestamp", System.currentTimeMillis());
            pushToAgent(agentId, response);
            LOGGER.info("坐席注册成功: agentId={}, agentName={}", agentId, agentName);
        }
    }

    /**
     * 处理聊天消息
     */
    private void handleChatMessage(String agentId, Map<String, Object> msgMap) {
        String sessionId = (String) msgMap.get("sessionId");
        String content = (String) msgMap.get("content");

        if (sessionId == null || content == null) {
            LOGGER.warn("聊天消息缺少必要字段");
            return;
        }

        // 创建聊天消息
        ChatMessage chatMessage = ChatMessage.fromAgent(sessionId, agentId,
                agentManager.getAgentName(agentId), content);

        // 发送到前置
        agentManager.sendChatMessageToRouter(agentId, chatMessage, sessionId);

        LOGGER.debug("坐席聊天消息已发送: agentId={}, sessionId={}", agentId, sessionId);
    }

    /**
     * 处理状态更新
     */
    private void handleStatusUpdate(String agentId, Map<String, Object> msgMap) {
        String status = (String) msgMap.get("status");
        if (status != null) {
            AgentStatus agentStatus = AgentStatus.valueOf(status.toUpperCase());
            agentManager.updateAgentStatus(agentId, agentStatus);
        }
    }

    /**
     * 处理会话接受
     */
    private void handleSessionAccept(String agentId, Map<String, Object> msgMap) {
        String sessionId = (String) msgMap.get("sessionId");
        if (sessionId != null) {
            sessionRegistry.registerSession(agentId, sessionId);
            agentManager.incrementSessionCount(agentId);
            LOGGER.info("坐席接受会话: agentId={}, sessionId={}", agentId, sessionId);
        }
    }

    /**
     * 处理会话关闭
     */
    private void handleSessionClose(String agentId, Map<String, Object> msgMap) {
        String sessionId = (String) msgMap.get("sessionId");
        if (sessionId != null) {
            sessionRegistry.unregisterSession(agentId, sessionId);
            agentManager.decrementSessionCount(agentId);
            LOGGER.info("坐席关闭会话: agentId={}, sessionId={}", agentId, sessionId);
        }
    }

    /**
     * 处理心跳 PING
     */
    private void handlePing(WebSocketSession session) {
        Map<String, Object> pong = new java.util.HashMap<>();
        pong.put("type", "PONG");
        pong.put("timestamp", System.currentTimeMillis());
        sendMessage(session, pong);
    }

    /**
     * 推送消息给坐席
     */
    public void pushToAgent(String agentId, Object message) {
        WebSocketSession session = agentSessions.get(agentId);
        if (session == null || !session.isOpen()) {
            LOGGER.warn("坐席 WebSocket 会话不存在或已关闭: agentId={}", agentId);
            return;
        }

        try {
            String json = objectMapper.writeValueAsString(message);
            session.sendMessage(new TextMessage(json));
            LOGGER.debug("消息已推送给坐席: agentId={}", agentId);
        } catch (IOException e) {
            LOGGER.error("推送消息给坐席失败: agentId={}", agentId, e);
        }
    }

    /**
     * 推送会话分配给坐席
     */
    public void pushSessionAssign(String agentId, Session session) {
        Map<String, Object> msg = new java.util.HashMap<>();
        msg.put("type", "SESSION_ASSIGN");
        msg.put("sessionId", session.getSessionId());
        msg.put("customerId", session.getCustomerId());
        msg.put("customerName", session.getCustomerName());
        Map<String, Object> sessionData = new java.util.HashMap<>();
        sessionData.put("sessionId", session.getSessionId());
        sessionData.put("customerId", session.getCustomerId());
        sessionData.put("customerName", session.getCustomerName());
        sessionData.put("status", session.getStatus() != null ? session.getStatus().name() : "WAITING");
        sessionData.put("createTime", session.getCreateTime());
        msg.put("session", sessionData);
        msg.put("timestamp", System.currentTimeMillis());
        pushToAgent(agentId, msg);
    }

    /**
     * 推送聊天消息给坐席
     */
    public void pushChatMessage(String agentId, ChatMessage chatMessage) {
        Map<String, Object> msg = new java.util.HashMap<>();
        msg.put("type", "CHAT_MESSAGE");
        msg.put("sessionId", chatMessage.getSessionId());
        msg.put("senderType", chatMessage.getSenderType().name());
        msg.put("senderId", chatMessage.getSenderId());
        msg.put("senderName", chatMessage.getSenderName());
        msg.put("content", chatMessage.getContent());
        msg.put("timestamp", chatMessage.getTimestamp());
        pushToAgent(agentId, msg);
    }

    /**
     * 推送会话关闭通知
     */
    public void pushSessionClose(String agentId, String sessionId) {
        Map<String, Object> msg = new java.util.HashMap<>();
        msg.put("type", "SESSION_CLOSE");
        msg.put("sessionId", sessionId);
        msg.put("timestamp", System.currentTimeMillis());
        pushToAgent(agentId, msg);
    }

    /**
     * 检查坐席是否在线
     */
    public boolean isAgentOnline(String agentId) {
        WebSocketSession session = agentSessions.get(agentId);
        return session != null && session.isOpen();
    }

    /**
     * 获取坐席 WebSocket 连接数
     */
    public int getConnectionCount() {
        return (int) agentSessions.values().stream().filter(WebSocketSession::isOpen).count();
    }

    /**
     * 获取所有在线坐席ID列表
     */
    public java.util.Set<String> getConnectedAgentIds() {
        return agentSessions.entrySet().stream()
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

    private String extractAgentId(WebSocketSession session) {
        // 从 URL 路径中提取 agentId: /ws/agent/{agentId}
        String path = session.getUri().getPath();
        String[] parts = path.split("/");
        if (parts.length > 0) {
            return parts[parts.length - 1];
        }
        return null;
    }

    private String extractQueryParam(WebSocketSession session, String param, String defaultValue) {
        String query = session.getUri().getQuery();
        if (query != null) {
            for (String p : query.split("&")) {
                if (p.startsWith(param + "=")) {
                    return p.substring(param.length() + 1);
                }
            }
        }
        return defaultValue;
    }

    private Object createConnectionSuccessMessage(String agentId) {
        Map<String, Object> msg = new java.util.HashMap<>();
        msg.put("type", "connected");
        msg.put("agentId", agentId);
        msg.put("timestamp", System.currentTimeMillis());
        return msg;
    }

    private Object createErrorMessage(String error) {
        Map<String, Object> msg = new java.util.HashMap<>();
        msg.put("type", "error");
        msg.put("message", error);
        return msg;
    }
}
