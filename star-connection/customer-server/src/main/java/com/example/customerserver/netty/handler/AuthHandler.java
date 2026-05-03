package com.example.customerserver.netty.handler;

import com.example.common.model.Message;
import com.example.common.model.MessageType;
import io.netty.channel.ChannelHandler;
import io.netty.channel.ChannelHandlerContext;
import io.netty.channel.ChannelInboundHandlerAdapter;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 客户端连接认证处理器
 */
@Component
@ChannelHandler.Sharable
public class AuthHandler extends ChannelInboundHandlerAdapter {
    private static final Logger LOGGER = LoggerFactory.getLogger(AuthHandler.class);
    private static final String AUTH_TOKEN_HEADER = "auth-token";
    private static final String DEFAULT_AUTH_TOKEN = "star-connection-token";

    // 简单认证 - 生产环境应使用适当的认证机制
    private final Map<String, Boolean> authenticatedChannels = new ConcurrentHashMap<>();

    @Override
    public void channelRead(ChannelHandlerContext ctx, Object msg) throws Exception {
        if (msg instanceof Message) {
            Message message = (Message) msg;

            // 检查通道是否已认证
            String channelId = ctx.channel().id().asShortText();
            if (authenticatedChannels.containsKey(channelId)) {
                // 已认证，传递给下一个处理器
                ctx.fireChannelRead(msg);
                return;
            }

            // 检查认证消息类型
            if (message.getType() == MessageType.AUTH) {
                handleAuthRequest(ctx, message);
            } else if (message.getType() == MessageType.REGISTER) {
                // 允许REGISTER消息无需认证（用于初始注册）
                handleRegisterRequest(ctx, message);
            } else if (message.getType() == MessageType.AGENT_REGISTER) {
                // 允许AGENT_REGISTER消息无需认证（坐席通过消息自包含认证）
                handleAgentRegisterRequest(ctx, message);
            } else {
                LOGGER.warn("通道 {} 发送未授权消息: {}", channelId, message.getType());
                ctx.close();
            }
        } else {
            ctx.fireChannelRead(msg);
        }
    }

    private void handleAuthRequest(ChannelHandlerContext ctx, Message authMessage) {
        String channelId = ctx.channel().id().asShortText();
        String token = authMessage.getHeader(AUTH_TOKEN_HEADER);

        // 简单令牌验证
        if (DEFAULT_AUTH_TOKEN.equals(token)) {
            authenticatedChannels.put(channelId, true);
            LOGGER.info("通道 {} 认证成功", channelId);

            // 发送认证成功响应
            Message response = new Message(MessageType.AUTH, "center", authMessage.getSource());
            response.setMessageId(authMessage.getMessageId());
            response.addHeader("status", "success");
            ctx.writeAndFlush(response);
        } else {
            LOGGER.warn("通道 {} 认证失败: 无效令牌", channelId);

            // 发送认证失败响应
            Message response = new Message(MessageType.AUTH, "center", authMessage.getSource());
            response.setMessageId(authMessage.getMessageId());
            response.addHeader("status", "failure");
            response.addHeader("reason", "invalid token");
            ctx.writeAndFlush(response);
            ctx.close();
        }
    }

    private void handleRegisterRequest(ChannelHandlerContext ctx, Message registerMessage) {
        String channelId = ctx.channel().id().asShortText();
        String serviceId = registerMessage.getSource();

        // 对于REGISTER消息，允许通过但在MessageHandler处理注册后标记通道为已认证
        LOGGER.info("处理通道 {} 上服务 {} 的注册", serviceId, channelId);
        ctx.fireChannelRead(registerMessage);
    }

    private void handleAgentRegisterRequest(ChannelHandlerContext ctx, Message agentRegisterMessage) {
        String channelId = ctx.channel().id().asShortText();
        String source = agentRegisterMessage.getSource();

        // 允许AGENT_REGISTER消息通过，由MessageHandler处理
        LOGGER.info("处理通道 {} 上来自 {} 的坐席注册", channelId, source);
        ctx.fireChannelRead(agentRegisterMessage);
    }

    @Override
    public void channelInactive(ChannelHandlerContext ctx) throws Exception {
        String channelId = ctx.channel().id().asShortText();
        authenticatedChannels.remove(channelId);
        LOGGER.debug("通道 {} 已从认证缓存中移除", channelId);
        super.channelInactive(ctx);
    }

    @Override
    public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) throws Exception {
        LOGGER.error("认证处理器异常", cause);
        ctx.close();
    }

    public boolean isAuthenticated(String channelId) {
        return authenticatedChannels.containsKey(channelId);
    }

    public void markAsAuthenticated(String channelId) {
        authenticatedChannels.put(channelId, true);
    }

    /**
     * 获取已认证通道数量
     */
    public int getAuthenticatedChannelCount() {
        return authenticatedChannels.size();
    }
}