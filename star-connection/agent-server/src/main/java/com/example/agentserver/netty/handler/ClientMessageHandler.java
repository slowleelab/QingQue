package com.example.agentserver.netty.handler;

import com.example.agentserver.agent.AgentManager;
import com.example.agentserver.agent.AgentSessionRegistry;
import com.example.agentserver.config.AgentServerProperties;
import com.example.agentserver.netty.manager.ConnectionManager;
import com.example.agentserver.websocket.AgentWebSocketHandler;
import com.example.agentserver.zookeeper.CustomerBindingQuery;
import com.example.common.model.*;
import com.example.common.util.MessageIdGenerator;
import com.example.agentserver.session.SessionStore;
import com.fasterxml.jackson.core.JsonProcessingException;
import io.netty.channel.ChannelHandler;
import io.netty.channel.ChannelHandlerContext;
import io.netty.channel.ChannelInboundHandlerAdapter;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Lazy;
import org.springframework.stereotype.Component;

import jakarta.annotation.PostConstruct;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 客户端消息处理器，用于处理来自路由节点的消息
 */
@Component
@ChannelHandler.Sharable
public class ClientMessageHandler extends ChannelInboundHandlerAdapter {
    private static final Logger LOGGER = LoggerFactory.getLogger(ClientMessageHandler.class);

    private final AgentServerProperties clientProperties;
    private final ConnectionManager connectionManager;
    private final ExecutorService messageProcessor;
    private final Map<String, MessageCallback> callbacks = new ConcurrentHashMap<>();

    // Agent backend components (optional, may be null)
    private AgentManager agentManager;
    private AgentWebSocketHandler agentWebSocketHandler;
    private AgentSessionRegistry agentSessionRegistry;
    private CustomerBindingQuery customerBindingQuery;
    private SessionStore sessionStore;

    @Autowired
    public ClientMessageHandler(AgentServerProperties clientProperties, @Lazy ConnectionManager connectionManager) {
        this.clientProperties = clientProperties;
        this.connectionManager = connectionManager;
        this.messageProcessor = Executors.newFixedThreadPool(4);
    }

    @Autowired(required = false)
    public void setAgentManager(AgentManager agentManager) {
        this.agentManager = agentManager;
    }

    @Autowired(required = false)
    public void setAgentWebSocketHandler(AgentWebSocketHandler agentWebSocketHandler) {
        this.agentWebSocketHandler = agentWebSocketHandler;
    }

    @Autowired(required = false)
    public void setAgentSessionRegistry(AgentSessionRegistry agentSessionRegistry) {
        this.agentSessionRegistry = agentSessionRegistry;
    }

    @Autowired(required = false)
    public void setCustomerBindingQuery(CustomerBindingQuery customerBindingQuery) {
        this.customerBindingQuery = customerBindingQuery;
    }

    @Autowired(required = false)
    public void setSessionStore(SessionStore sessionStore) {
        this.sessionStore = sessionStore;
    }

    @PostConstruct
    public void init() {
        LOGGER.info("客户端消息处理器已为服务 {} 初始化", clientProperties.getServiceId());
    }

    @Override
    public void channelActive(ChannelHandlerContext ctx) throws Exception {
        LOGGER.info("通道已激活，正在发送认证...");

        // 发送认证消息
        Message authMessage = new Message(MessageType.AUTH,
                clientProperties.getServiceId(),
                "router");
        authMessage.setMessageId(MessageIdGenerator.generate());
        authMessage.addHeader("auth-token", clientProperties.getAuthToken());

        ctx.writeAndFlush(authMessage);

        // 认证后发送注册消息（简化处理 - 实际场景应等待认证响应）
        sendRegistration(ctx);

        super.channelActive(ctx);
    }

    private void sendRegistration(ChannelHandlerContext ctx) {
        Message registerMessage = new Message(MessageType.REGISTER,
                clientProperties.getServiceId(),
                "router");
        registerMessage.setMessageId(MessageIdGenerator.generate());
        registerMessage.addHeader("service-name", clientProperties.getServiceName());
        registerMessage.addHeader("timestamp", String.valueOf(System.currentTimeMillis()));

        ctx.writeAndFlush(registerMessage);
        LOGGER.info("服务 {} 的注册消息已发送", clientProperties.getServiceId());
    }

    @Override
    public void channelRead(ChannelHandlerContext ctx, Object msg) throws Exception {
        if (msg instanceof Message) {
            Message message = (Message) msg;
            MessageType type = message.getType();

            // 异步处理消息
            messageProcessor.submit(() -> processMessage(message, type));
        } else {
            LOGGER.warn("收到非Message对象: {}", msg.getClass().getName());
        }
    }

    private void processMessage(Message message, MessageType type) {
        switch (type) {
            case AUTH:
                handleAuthResponse(message);
                break;
            case RESPONSE:
                handleResponse(message);
                break;
            case REQUEST:
                handleRequest(message);
                break;
            case NOTIFY:
                handleNotification(message);
                break;
            case HEARTBEAT:
                handleHeartbeatResponse(message);
                break;
            // ========== 在线客服系统消息处理 ==========
            case SESSION_ASSIGN:
                handleSessionAssign(message);
                break;
            case SESSION_CLOSE:
                handleSessionClose(message);
                break;
            case CHAT_MESSAGE:
                handleChatMessage(message);
                break;
            default:
                LOGGER.warn("未处理的消息类型: {}", type);
        }
    }

    private void handleAuthResponse(Message authResponse) {
        String status = authResponse.getHeader("status");
        if ("success".equals(status)) {
            LOGGER.info("认证成功");
        } else {
            LOGGER.error("认证失败: {}", authResponse.getHeader("reason"));
            // 可以使用不同的凭据触发重连
        }
    }

    private void handleResponse(Message response) {
        // 首先尝试通过 original_message_id 头部查找回调
        String originalMessageId = response.getHeader("original_message_id");
        MessageCallback callback = null;
        
        if (originalMessageId != null) {
            callback = callbacks.remove(originalMessageId);
        }
        
        // 如果没有通过 original_message_id 找到，尝试用消息ID本身
        if (callback == null) {
            callback = callbacks.remove(response.getMessageId());
        }

        if (callback != null) {
            try {
                callback.onResponse(response);
            } catch (Exception e) {
                LOGGER.error("处理响应回调时出错", e);
            }
        } else {
            LOGGER.debug("未找到响应 {} 的回调", response.getMessageId());
            // 处理通用响应
            String status = response.getHeader("status");
            if ("registered".equals(status)) {
                LOGGER.info("服务注册已确认");
            } else if ("error".equals(status)) {
                LOGGER.error("错误响应: {}", response.getHeader("error_code"));
            }
        }
    }

    private void handleRequest(Message request) {
        LOGGER.info("收到来自 {} 的请求: {}", request.getSource(), request.getMessageId());

        // 处理请求并发送响应
        Message response = new Message(MessageType.RESPONSE,
                clientProperties.getServiceId(),
                request.getSource());
        response.setMessageId(MessageIdGenerator.generate());
        response.addHeader("original_message_id", request.getMessageId());
        response.addHeader("status", "processed");
        response.setPayload("请求处理成功");

        connectionManager.sendMessage(response);
    }

    private void handleNotification(Message notification) {
        LOGGER.info("收到来自 {} 的通知: {}", notification.getSource(), notification.getMessageId());
        // 处理通知（无需响应）
    }

    private void handleHeartbeatResponse(Message heartbeat) {
        LOGGER.debug("收到路由节点的心跳响应");
        // 如需要，更新最后心跳时间戳
    }

    @Override
    public void channelInactive(ChannelHandlerContext ctx) throws Exception {
        LOGGER.warn("通道非活动状态，连接丢失");
        super.channelInactive(ctx);
    }

    @Override
    public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) throws Exception {
        LOGGER.error("客户端消息处理器异常", cause);
        ctx.close();
    }

    /**
     * 发送请求消息并带回调
     */
    public void sendRequest(Message request, MessageCallback callback) {
        if (request.getMessageId() == null) {
            request.setMessageId(MessageIdGenerator.generate());
        }

        callbacks.put(request.getMessageId(), callback);
        connectionManager.sendMessage(request);
    }

    /**
     * 发送请求消息不带回调
     */
    public void sendRequest(Message request) {
        if (request.getMessageId() == null) {
            request.setMessageId(MessageIdGenerator.generate());
        }
        connectionManager.sendMessage(request);
    }

    /**
     * 发送通知消息
     */
    public void sendNotification(Message notification) {
        if (notification.getMessageId() == null) {
            notification.setMessageId(MessageIdGenerator.generate());
        }
        connectionManager.sendMessage(notification);
    }

    // ========== 在线客服系统消息处理方法 ==========

    /**
     * 处理会话分配消息（来自前置，分配客户给坐席）
     */
    private void handleSessionAssign(Message message) {
        if (agentWebSocketHandler == null) {
            LOGGER.warn("坐席 WebSocket 处理器未初始化，忽略会话分配消息");
            return;
        }

        String sessionId = message.getHeader("sessionId");
        String agentId = message.getHeader("agentId");
        String customerId = message.getHeader("customerId");

        LOGGER.info("收到会话分配: sessionId={}, agentId={}, customerId={}", sessionId, agentId, customerId);

        try {
            Session session = message.getPayloadAs(Session.class);
            if (session != null && agentId != null) {
                // 保存会话信息（包含 routerId）
                if (sessionStore != null) {
                    sessionStore.save(session);
                    LOGGER.debug("会话信息已保存: sessionId={}, routerId={}", sessionId, session.getRouterId());
                }

                // 注册 sessionId -> customerId 映射（用于查询客户绑定的路由节点）
                if (customerBindingQuery != null && session.getCustomerId() != null) {
                    customerBindingQuery.registerSessionCustomer(sessionId, session.getCustomerId());
                    LOGGER.debug("会话客户映射已注册: sessionId={}, customerId={}", sessionId, session.getCustomerId());
                }

                // 推送给坐席
                agentWebSocketHandler.pushSessionAssign(agentId, session);

                // 注册会话
                if (agentSessionRegistry != null) {
                    agentSessionRegistry.registerSession(agentId, sessionId);
                }
            }
        } catch (JsonProcessingException e) {
            LOGGER.error("解析会话分配消息失败", e);
        }
    }

    /**
     * 处理会话关闭消息
     */
    private void handleSessionClose(Message message) {
        if (agentWebSocketHandler == null) {
            LOGGER.warn("坐席 WebSocket 处理器未初始化，忽略会话关闭消息");
            return;
        }

        String sessionId = message.getHeader("sessionId");
        String agentId = message.getHeader("agentId");

        LOGGER.info("收到会话关闭: sessionId={}, agentId={}", sessionId, agentId);

        if (agentId != null && sessionId != null) {
            // 推送给坐席
            agentWebSocketHandler.pushSessionClose(agentId, sessionId);

            // 注销会话
            if (agentSessionRegistry != null) {
                agentSessionRegistry.unregisterSession(agentId, sessionId);
            }

            // 减少会话数
            if (agentManager != null) {
                agentManager.decrementSessionCount(agentId);
            }
        }
    }

    /**
     * 处理聊天消息（来自前置，客户消息转发给坐席）
     */
    private void handleChatMessage(Message message) {
        if (agentWebSocketHandler == null) {
            LOGGER.warn("坐席 WebSocket 处理器未初始化，忽略聊天消息");
            return;
        }

        String sessionId = message.getHeader("sessionId");
        String agentId = message.getHeader("agentId");

        try {
            ChatMessage chatMessage = message.getPayloadAs(ChatMessage.class);
            if (chatMessage != null && agentId != null) {
                // 推送给坐席
                agentWebSocketHandler.pushChatMessage(agentId, chatMessage);
                LOGGER.debug("聊天消息已推送给坐席: agentId={}, sessionId={}", agentId, sessionId);
            }
        } catch (JsonProcessingException e) {
            LOGGER.error("解析聊天消息失败", e);
        }
    }

    /**
     * 请求响应回调接口
     */
    public interface MessageCallback {
        void onResponse(Message response);
    }
}
