package com.example.customerserver.netty;

import com.example.customerserver.config.NettyProperties;
import io.netty.bootstrap.ServerBootstrap;
import io.netty.channel.ChannelFuture;
import io.netty.channel.ChannelOption;
import io.netty.channel.EventLoopGroup;
import io.netty.channel.nio.NioEventLoopGroup;
import io.netty.channel.socket.nio.NioServerSocketChannel;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;

/**
 * 中心节点 Netty 服务器
 */
@Component
public class NettyServer {
    private static final Logger LOGGER = LoggerFactory.getLogger(NettyServer.class);

    private final NettyProperties nettyProperties;
    private final ServerChannelInitializer channelInitializer;

    private EventLoopGroup bossGroup;
    private EventLoopGroup workerGroup;
    private ChannelFuture channelFuture;
    private long startTime;

    @Autowired
    public NettyServer(NettyProperties nettyProperties, ServerChannelInitializer channelInitializer) {
        this.nettyProperties = nettyProperties;
        this.channelInitializer = channelInitializer;
    }

    @PostConstruct
    public void start() throws InterruptedException {
        LOGGER.info("正在端口 {} 上启动 Netty 服务器...", nettyProperties.getPort());
        startTime = System.currentTimeMillis();

        bossGroup = new NioEventLoopGroup(nettyProperties.getBossThreads());
        workerGroup = new NioEventLoopGroup(nettyProperties.getWorkerThreads());

        try {
            ServerBootstrap bootstrap = new ServerBootstrap();
            bootstrap.group(bossGroup, workerGroup)
                    .channel(NioServerSocketChannel.class)
                    .childHandler(channelInitializer)
                    .option(ChannelOption.SO_BACKLOG, nettyProperties.getSoBacklog())
                    .childOption(ChannelOption.SO_KEEPALIVE, nettyProperties.isKeepAlive())
                    .childOption(ChannelOption.TCP_NODELAY, true);

            channelFuture = bootstrap.bind(nettyProperties.getPort()).sync();
            LOGGER.info("Netty 服务器在端口 {} 上启动成功", nettyProperties.getPort());
        } catch (Exception e) {
            LOGGER.error("Netty 服务器启动失败", e);
            stop();
            throw e;
        }
    }

    @PreDestroy
    public void stop() {
        LOGGER.info("正在停止 Netty 服务器...");

        if (channelFuture != null) {
            channelFuture.channel().close();
        }

        if (workerGroup != null) {
            workerGroup.shutdownGracefully();
        }

        if (bossGroup != null) {
            bossGroup.shutdownGracefully();
        }

        LOGGER.info("Netty 服务器已停止");
    }

    public boolean isRunning() {
        return channelFuture != null && channelFuture.channel().isActive();
    }

    public int getPort() {
        return nettyProperties.getPort();
    }

    public long getStartTime() {
        return startTime;
    }
}