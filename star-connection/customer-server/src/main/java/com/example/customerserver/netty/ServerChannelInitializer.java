package com.example.customerserver.netty;

import com.example.customerserver.config.NettyProperties;
import com.example.customerserver.netty.handler.AuthHandler;
import com.example.customerserver.netty.handler.HeartbeatHandler;
import com.example.customerserver.netty.handler.MessageHandler;
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
 * 服务器通道初始化器
 */
@Component
public class ServerChannelInitializer extends ChannelInitializer<SocketChannel> {
    private final NettyProperties nettyProperties;
    private final AuthHandler authHandler;
    private final MessageHandler messageHandler;
    private final HeartbeatHandler heartbeatHandler;

    @Autowired
    public ServerChannelInitializer(NettyProperties nettyProperties,
                                   AuthHandler authHandler,
                                   MessageHandler messageHandler,
                                   HeartbeatHandler heartbeatHandler) {
        this.nettyProperties = nettyProperties;
        this.authHandler = authHandler;
        this.messageHandler = messageHandler;
        this.heartbeatHandler = heartbeatHandler;
    }

    @Override
    protected void initChannel(SocketChannel ch) throws Exception {
        // 添加空闲状态处理器用于心跳
        ch.pipeline().addLast(new IdleStateHandler(
                nettyProperties.getIdleTimeoutSeconds(),
                0, 0, TimeUnit.SECONDS));

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

        // 添加认证处理器
        ch.pipeline().addLast(authHandler);

        // 添加心跳处理器
        ch.pipeline().addLast(heartbeatHandler);

        // 添加消息处理器
        ch.pipeline().addLast(messageHandler);
    }
}