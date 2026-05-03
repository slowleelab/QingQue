package com.example.customerserver.netty.handler;

import com.example.customerserver.agent.AgentRegistry;
import com.example.customerserver.netty.manager.ConnectionManager;
import com.example.customerserver.session.SessionManager;
import com.example.customerserver.websocket.CustomerWebSocketHandler;
import com.example.common.model.*;
import com.example.common.util.MessageIdGenerator;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.netty.channel.ChannelHandler;
import io.netty.channel.ChannelHandlerContext;
import io.netty.channel.ChannelInboundHandlerAdapter;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 主消息处理器，用于处理和路由消息
 */
@Component
@ChannelHandler.Sharable
public class MessageHandler extends ChannelInboundHandlerAdapter {
    private static final Logger LOGGER = LoggerFactory.getLogger(MessageHandler.class);

    private final ConnectionManager connectionManager;
    private final AuthHandler authHandler;
    private final AgentRegistry agentRegistry;
    private final SessionManager sessionManager;
    private final CustomerWebSocketHandler customerWebSocketHandler;
    private final ObjectMapper objectMapper;

    // 后台节点ID -> Channel映射
    private final Map<String, ChannelHandlerContext> backendChannels = new ConcurrentHashMap<>();

    @Autowired
    public MessageHandler(ConnectionManager connectionManager,
                          AuthHandler authHandler,
                          AgentRegistry agentRegistry,
                          SessionManager sessionManager,
                          CustomerWebSocketHandler customerWebSocketHandler) {
        this.connectionManager = connectionManager;
        this.authHandler = authHandler;
        this.agentRegistry = agentRegistry;
        this.sessionManager = sessionManager;
        this.customerWebSocketHandler = customerWebSocketHandler;
        this.objectMapper = new ObjectMapper();
    }

    @Override
    public void channelRead(ChannelHandlerContext ctx, Object msg) throws Exception {
        if (msg instanceof Message) {
            Message message = (Message) msg;
            MessageType type = message.getType();

            switch (type) {
                case REGISTER:
                    handleRegister(ctx, message);
                    break;
                case REQUEST:
                case NOTIFY:
                    handleMessageRouting(ctx, message);
                    break;
                case RESPONSE:
                    handleResponse(ctx, message);
                    break;
                // ========== 在线客服系统消息处理 ==========
                case AGENT_REGISTER:
                    handleAgentRegister(ctx, message);
                    break;
                case AGENT_STATUS:
                    handleAgentStatus(ctx, message);
                    break;
                case SESSION_CREATE:
                    handleSessionCreate(ctx, message);
                    break;
                case SESSION_ASSIGN:
                    handleSessionAssign(ctx, message);
                    break;
                case SESSION_CLOSE:
                    handleSessionClose(ctx, message);
                    break;
                case CHAT_MESSAGE:
                    handleChatMessage(ctx, message);
                    break;
                default:
                    LOGGER.warn("未处理的消息类型: {}", type);
            }
        } else {
            LOGGER.warn("收到非Message对象: {}", msg.getClass().getName());
            ctx.fireChannelRead(msg);
        }
    }

    private void handleRegister(ChannelHandlerContext ctx, Message registerMessage) {
        String serviceId = registerMessage.getSource();
        String channelId = ctx.channel().id().asShortText();

        // 注册成功后标记通道为已认证
        authHandler.markAsAuthenticated(channelId);

        // 注册连接
        connectionManager.registerConnection(serviceId, ctx);

        // 发送注册响应
        Message response = new Message(MessageType.RESPONSE, "router", serviceId);
        response.setMessageId(registerMessage.getMessageId());
        response.addHeader("status", "registered");
        response.addHeader("timestamp", String.valueOf(System.currentTimeMillis()));

        ctx.writeAndFlush(response);
        LOGGER.info("服务 {} 注册成功", serviceId);
    }

    private void handleMessageRouting(ChannelHandlerContext ctx, Message message) {
        String targetService = message.getTarget();
        String sourceService = message.getSource();

        // 检查目标服务是否已连接
        if (connectionManager.isConnected(targetService)) {
            // 将消息转发到目标服务
            boolean sent = connectionManager.sendMessage(targetService, message);
            if (sent) {
                LOGGER.debug("消息从 {} 路由到 {}", sourceService, targetService);

                // 对于REQUEST消息，可以跟踪待处理请求
                if (message.getType() == MessageType.REQUEST) {
                    // TODO: 添加请求跟踪用于响应关联
                }
            } else {
                LOGGER.error("发送消息到 {} 失败", targetService);
                sendErrorResponse(ctx, message, "target_not_available");
            }
        } else {
            LOGGER.warn("目标服务 {} 未连接", targetService);
            sendErrorResponse(ctx, message, "target_not_connected");
        }
    }

    private void handleResponse(ChannelHandlerContext ctx, Message response) {
        // 响应通常发回给原始请求者
        // 在星型拓扑中，响应通过中心节点回流
        String targetService = response.getTarget();

        if (connectionManager.isConnected(targetService)) {
            connectionManager.sendMessage(targetService, response);
            LOGGER.debug("响应路由到 {}", targetService);
        } else {
            LOGGER.warn("无法将响应路由到已断开的服务: {}", targetService);
        }
    }

    private void sendErrorResponse(ChannelHandlerContext ctx, Message originalMessage, String errorCode) {
        Message errorResponse = new Message(MessageType.RESPONSE, "router", originalMessage.getSource());
        errorResponse.setMessageId(MessageIdGenerator.generate());
        errorResponse.addHeader("status", "error");
        errorResponse.addHeader("error_code", errorCode);
        errorResponse.addHeader("original_message_id", originalMessage.getMessageId());

        ctx.writeAndFlush(errorResponse);
    }

    @Override
    public void channelInactive(ChannelHandlerContext ctx) throws Exception {
        connectionManager.removeConnection(ctx);
        super.channelInactive(ctx);
    }

    @Override
    public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) throws Exception {
        LOGGER.error("消息处理器异常", cause);
        ctx.close();
    }

    // ========== 在线客服系统消息处理方法 ==========

    /**
     * 处理坐席注册消息（来自坐席后台）
     */
    private void handleAgentRegister(ChannelHandlerContext ctx, Message message) {
        String backendId = message.getSource();
        String channelId = ctx.channel().id().asShortText();

        // 标记为已认证
        authHandler.markAsAuthenticated(channelId);

        // 注册后台节点连接
        connectionManager.registerConnection(backendId, ctx);
        backendChannels.put(backendId, ctx);

        // 解析坐席信息
        try {
            Agent agent = message.getPayloadAs(Agent.class);
            if (agent != null) {
                agent.setBackendId(backendId);
                agent.setOnlineTime(System.currentTimeMillis());
                agentRegistry.registerAgent(agent);
                LOGGER.info("坐席注册: agentId={}, backendId={}", agent.getAgentId(), backendId);

                // 发送注册成功响应
                Message response = new Message(MessageType.RESPONSE, "router", backendId);
                response.setMessageId(message.getMessageId());
                response.addHeader("status", "agent_registered");
                response.addHeader("agentId", agent.getAgentId());
                ctx.writeAndFlush(response);

                // 坐席上线后，处理等待中的会话
                int waitingCount = sessionManager.getWaitingSessions().size();
                if (waitingCount > 0) {
                    LOGGER.info("坐席 {} 上线，尝试处理 {} 个等待中的会话", agent.getAgentId(), waitingCount);
                    sessionManager.processWaitingSessions();
                }
            }
        } catch (JsonProcessingException e) {
            LOGGER.error("解析坐席注册消息失败", e);
            sendErrorResponse(ctx, message, "invalid_agent_info");
        }
    }

    /**
     * 处理坐席状态更新消息
     */
    private void handleAgentStatus(ChannelHandlerContext ctx, Message message) {
        String agentId = message.getHeader("agentId");
        String statusStr = message.getHeader("status");

        if (agentId == null || statusStr == null) {
            LOGGER.warn("坐席状态更新消息缺少必要字段");
            return;
        }

        try {
            AgentStatus status = AgentStatus.fromCode(Integer.parseInt(statusStr));

            // 如果坐席离线，从注册表中删除，而不是只更新状态
            if (status == AgentStatus.OFFLINE) {
                agentRegistry.unregisterAgent(agentId);
                LOGGER.info("坐席已注销（离线）: agentId={}", agentId);
            } else {
                agentRegistry.updateAgentStatus(agentId, status);
                LOGGER.info("坐席状态更新: agentId={}, status={}", agentId, status);
            }

            // 发送确认响应
            Message response = new Message(MessageType.RESPONSE, "router", message.getSource());
            response.setMessageId(message.getMessageId());
            response.addHeader("status", "updated");
            ctx.writeAndFlush(response);
        } catch (Exception e) {
            LOGGER.error("处理坐席状态更新失败", e);
        }
    }

    /**
     * 处理会话创建消息
     */
    private void handleSessionCreate(ChannelHandlerContext ctx, Message message) {
        // 通常会话创建是通过 REST API 或 WebSocket，这里处理来自后台的创建请求
        try {
            Session session = message.getPayloadAs(Session.class);
            if (session != null) {
                LOGGER.info("收到会话创建请求: sessionId={}", session.getSessionId());
                // 会话创建由 SessionManager 处理，这里仅记录
            }
        } catch (JsonProcessingException e) {
            LOGGER.error("解析会话创建消息失败", e);
        }
    }

    /**
     * 处理会话分配确认消息
     */
    private void handleSessionAssign(ChannelHandlerContext ctx, Message message) {
        String sessionId = message.getHeader("sessionId");
        String agentId = message.getHeader("agentId");
        String status = message.getHeader("status");

        LOGGER.debug("会话分配确认: sessionId={}, agentId={}, status={}", sessionId, agentId, status);
        // 更新会话状态等处理
    }

    /**
     * 处理会话关闭消息
     */
    private void handleSessionClose(ChannelHandlerContext ctx, Message message) {
        String sessionId = message.getHeader("sessionId");
        if (sessionId != null) {
            sessionManager.closeSession(sessionId);
            LOGGER.info("会话已关闭: sessionId={}", sessionId);
        }
    }

    /**
     * 处理聊天消息（来自坐席后台，需要转发给客户）
     */
    private void handleChatMessage(ChannelHandlerContext ctx, Message message) {
        try {
            ChatMessage chatMessage = message.getPayloadAs(ChatMessage.class);
            String sessionId = message.getHeader("sessionId");

            if (chatMessage == null || sessionId == null) {
                LOGGER.warn("聊天消息缺少必要字段");
                return;
            }

            chatMessage.setSessionId(sessionId);

            // 推送给客户
            customerWebSocketHandler.pushToCustomer(sessionId, chatMessage);

            LOGGER.debug("聊天消息已推送给客户: sessionId={}", sessionId);

        } catch (JsonProcessingException e) {
            LOGGER.error("解析聊天消息失败", e);
        }
    }
}