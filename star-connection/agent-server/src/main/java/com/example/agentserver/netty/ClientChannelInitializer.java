package com.example.agentserver.netty;

import com.example.agentserver.config.NettyAgentServerProperties;
import com.example.agentserver.netty.handler.ClientMessageHandler;
import com.example.agentserver.netty.handler.HeartbeatSender;
import com.example.common.codec.JsonMessageDecoder;
import com.example.common.codec.JsonMessageEncoder;
import io.netty.channel.ChannelInitializer;
import io.netty.channel.socket.SocketChannel;
import io.netty.handler.codec.LengthFieldBasedFrameDecoder;
import io.netty.handler.codec.LengthFieldPrepender;
import io.netty.handler.timeout.IdleStateHandler;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.util.concurrent.TimeUnit;

/**
 * 客户端通道初始化器
 */
@Component
public class ClientChannelInitializer extends ChannelInitializer<SocketChannel> {
    private final NettyAgentServerProperties nettyProperties;
    private final ClientMessageHandler messageHandler;
    private final HeartbeatSender heartbeatSender;

    @Autowired
    public ClientChannelInitializer(NettyAgentServerProperties nettyProperties,
                                   ClientMessageHandler messageHandler,
                                   HeartbeatSender heartbeatSender) {
        this.nettyProperties = nettyProperties;
        this.messageHandler = messageHandler;
        this.heartbeatSender = heartbeatSender;
    }

    @Override
    protected void initChannel(SocketChannel ch) throws Exception {
        // 添加空闲状态处理器用于发送心跳
        ch.pipeline().addLast(new IdleStateHandler(
                0, // 读空闲
                nettyProperties.getHeartbeatIntervalSeconds(), // 写空闲（触发心跳）
                0, // 全空闲
                TimeUnit.SECONDS));

        // 添加帧解码器/编码器
        ch.pipeline().addLast(new LengthFieldBasedFrameDecoder(
                10 * 1024 * 1024, // 最大帧长度: 10MB
                0, // 长度字段偏移
                4, // 长度字段长度
                0, // 长度调整
                4  // 初始剥离字节数
        ));
        ch.pipeline().addLast(new LengthFieldPrepender(4));

        // 添加JSON编解码器
        ch.pipeline().addLast(new JsonMessageDecoder());
        ch.pipeline().addLast(new JsonMessageEncoder());

        // 添加心跳发送器
        ch.pipeline().addLast(heartbeatSender);

        // 添加消息处理器
        ch.pipeline().addLast(messageHandler);
    }
}