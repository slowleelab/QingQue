package com.example.customerserver.netty.handler;

import com.example.common.model.Message;
import com.example.common.model.MessageType;
import io.netty.channel.ChannelHandler;
import io.netty.channel.ChannelHandlerContext;
import io.netty.channel.ChannelInboundHandlerAdapter;
import io.netty.handler.timeout.IdleState;
import io.netty.handler.timeout.IdleStateEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * 心跳处理器，用于检测空闲连接
 */
@Component
@ChannelHandler.Sharable
public class HeartbeatHandler extends ChannelInboundHandlerAdapter {
    private static final Logger LOGGER = LoggerFactory.getLogger(HeartbeatHandler.class);

    @Override
    public void userEventTriggered(ChannelHandlerContext ctx, Object evt) throws Exception {
        if (evt instanceof IdleStateEvent) {
            IdleStateEvent event = (IdleStateEvent) evt;
            if (event.state() == IdleState.READER_IDLE) {
                LOGGER.warn("通道 {} 读取空闲，关闭连接", ctx.channel().id().asShortText());
                ctx.close();
            }
        } else {
            super.userEventTriggered(ctx, evt);
        }
    }

    @Override
    public void channelRead(ChannelHandlerContext ctx, Object msg) throws Exception {
        if (msg instanceof Message) {
            Message message = (Message) msg;

            // 处理心跳消息
            if (message.getType() == MessageType.HEARTBEAT) {
                handleHeartbeat(ctx, message);
                return; // 不将心跳传递给下一个处理器
            }
        }

        ctx.fireChannelRead(msg);
    }

    private void handleHeartbeat(ChannelHandlerContext ctx, Message heartbeat) {
        String channelId = ctx.channel().id().asShortText();
        LOGGER.debug("从通道 {} 收到心跳: {}", channelId, heartbeat.getSource());

        // 发送心跳响应
        Message response = new Message(MessageType.HEARTBEAT, "center", heartbeat.getSource());
        response.setMessageId(heartbeat.getMessageId());
        response.addHeader("timestamp", String.valueOf(System.currentTimeMillis()));

        ctx.writeAndFlush(response);
        LOGGER.debug("向通道 {} 发送心跳响应", channelId);
    }

    @Override
    public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) throws Exception {
        LOGGER.error("心跳处理器异常", cause);
        ctx.close();
    }
}