package com.example.agentserver.netty.handler;

import com.example.agentserver.config.AgentServerProperties;
import com.example.common.model.Message;
import com.example.common.model.MessageType;
import com.example.common.util.MessageIdGenerator;
import io.netty.channel.ChannelHandler;
import io.netty.channel.ChannelHandlerContext;
import io.netty.channel.ChannelInboundHandlerAdapter;
import io.netty.handler.timeout.IdleState;
import io.netty.handler.timeout.IdleStateEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

/**
 * 客户端心跳发送器
 */
@Component
@ChannelHandler.Sharable
public class HeartbeatSender extends ChannelInboundHandlerAdapter {
    private static final Logger LOGGER = LoggerFactory.getLogger(HeartbeatSender.class);

    private final AgentServerProperties clientProperties;

    @Autowired
    public HeartbeatSender(AgentServerProperties clientProperties) {
        this.clientProperties = clientProperties;
    }

    @Override
    public void userEventTriggered(ChannelHandlerContext ctx, Object evt) throws Exception {
        if (evt instanceof IdleStateEvent) {
            IdleStateEvent event = (IdleStateEvent) evt;
            if (event.state() == IdleState.WRITER_IDLE) {
                sendHeartbeat(ctx);
            }
        } else {
            super.userEventTriggered(ctx, evt);
        }
    }

    private void sendHeartbeat(ChannelHandlerContext ctx) {
        if (ctx.channel().isActive()) {
            Message heartbeat = new Message(MessageType.HEARTBEAT,
                    clientProperties.getServiceId(),
                    "center");
            heartbeat.setMessageId(MessageIdGenerator.generate());
            heartbeat.addHeader("timestamp", String.valueOf(System.currentTimeMillis()));

            ctx.writeAndFlush(heartbeat);
            LOGGER.debug("向中心节点发送心跳");
        }
    }

    @Override
    public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) throws Exception {
        LOGGER.error("心跳发送器异常", cause);
        ctx.fireExceptionCaught(cause);
    }
}