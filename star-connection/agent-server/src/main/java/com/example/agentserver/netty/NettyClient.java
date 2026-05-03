package com.example.agentserver.netty;

import com.example.agentserver.config.ClientConnectionProperties;
import com.example.agentserver.config.AgentServerProperties;
import com.example.agentserver.config.NettyAgentServerProperties;
import com.example.agentserver.netty.manager.ConnectionManager;
import com.example.agentserver.netty.manager.ReconnectionManager;
import com.example.agentserver.zookeeper.RouterServiceDiscovery;
import org.apache.curator.x.discovery.ServiceInstance;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Lazy;
import org.springframework.stereotype.Component;

import io.netty.bootstrap.Bootstrap;
import io.netty.channel.Channel;
import io.netty.channel.ChannelFuture;
import io.netty.channel.ChannelOption;
import io.netty.channel.EventLoopGroup;
import io.netty.channel.nio.NioEventLoopGroup;
import io.netty.channel.socket.nio.NioSocketChannel;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * 连接路由节点的 Netty 客户端（单路由模式）
 * 支持通过 ZooKeeper 服务发现动态连接
 */
@Component
@ConditionalOnProperty(name = "client.connection.connect-to-all-routers", havingValue = "false", matchIfMissing = true)
public class NettyClient implements ConnectionManager {
    private static final Logger LOGGER = LoggerFactory.getLogger(NettyClient.class);

    private final NettyAgentServerProperties nettyProperties;
    private final AgentServerProperties clientProperties;
    private final ClientConnectionProperties connectionProperties;
    private final ClientChannelInitializer channelInitializer;
    private final ReconnectionManager reconnectionManager;
    private final RouterServiceDiscovery routerServiceDiscovery;

    private EventLoopGroup group;
    private final AtomicReference<Channel> channelRef = new AtomicReference<>();
    private volatile boolean connected = false;
    private volatile boolean shutdown = false;

    // 当前连接的路由节点
    private volatile String currentRouterId;
    private volatile String currentRouterHost;
    private volatile int currentRouterPort;

    // 服务发现刷新调度器
    private ScheduledExecutorService discoveryScheduler;

    @Autowired
    public NettyClient(NettyAgentServerProperties nettyProperties,
                       AgentServerProperties clientProperties,
                       ClientConnectionProperties connectionProperties,
                       ClientChannelInitializer channelInitializer,
                       ReconnectionManager reconnectionManager,
                       @Lazy RouterServiceDiscovery routerServiceDiscovery) {
        this.nettyProperties = nettyProperties;
        this.clientProperties = clientProperties;
        this.connectionProperties = connectionProperties;
        this.channelInitializer = channelInitializer;
        this.reconnectionManager = reconnectionManager;
        this.routerServiceDiscovery = routerServiceDiscovery;
    }

    @PostConstruct
    public void init() {
        LOGGER.info("正在为服务 {} 初始化 Netty 客户端（单路由模式）", clientProperties.getServiceId());

        // 添加服务变化监听器
        if (connectionProperties.isDiscoveryEnabled() && routerServiceDiscovery != null) {
            routerServiceDiscovery.addChangeListener(this::handleRouterInstancesChange);
        }

        // 初始连接
        connect();

        // 启动定期刷新
        if (connectionProperties.isDiscoveryEnabled()) {
            startDiscoveryRefresh();
        }
    }

    /**
     * 处理路由节点实例变化
     */
    private void handleRouterInstancesChange(List<ServiceInstance<Map<String, String>>> instances) {
        LOGGER.info("收到路由节点变化通知，当前有 {} 个路由节点", instances.size());

        if (instances.isEmpty()) {
            LOGGER.warn("没有可用的路由节点");
            // 如果当前有连接，可能需要断开
            return;
        }

        // 检查当前连接的路由节点是否还在列表中
        boolean currentCenterStillAvailable = false;
        for (ServiceInstance<Map<String, String>> instance : instances) {
            if (instance.getId().equals(currentRouterId)) {
                currentCenterStillAvailable = true;
                break;
            }
        }

        // 如果当前路由节点不可用了，或者还没有连接，则连接新的路由节点
        if (!currentCenterStillAvailable || !isConnected()) {
            LOGGER.info("需要连接到新的路由节点");
            connectToAvailableRouter(instances);
        }
    }

    /**
     * 连接到可用的路由节点
     */
    private void connectToAvailableRouter(List<ServiceInstance<Map<String, String>>> instances) {
        if (instances == null || instances.isEmpty()) {
            // 使用配置文件中的地址作为后备
            connectTo(nettyProperties.getHost(), nettyProperties.getPort(), null);
            return;
        }

        // 尝试连接第一个可用的路由节点
        for (ServiceInstance<Map<String, String>> instance : instances) {
            String host = instance.getAddress();
            int port = instance.getPort();
            String centerId = instance.getId();

            // 检查是否与当前连接相同
            if (centerId.equals(currentRouterId) && isConnected()) {
                LOGGER.debug("已经连接到路由节点 {}", centerId);
                return;
            }

            if (connectTo(host, port, centerId)) {
                return; // 连接成功
            }
        }

        // 所有路由节点都连接失败，使用配置文件中的地址
        LOGGER.warn("无法连接到任何发现的路由节点，尝试使用配置文件中的地址");
        connectTo(nettyProperties.getHost(), nettyProperties.getPort(), null);
    }

    /**
     * 连接到指定地址
     */
    private boolean connectTo(String host, int port, String centerId) {
        if (shutdown) {
            return false;
        }

        LOGGER.info("正在连接到路由节点 {} ({}:{})...", centerId != null ? centerId : "unknown", host, port);

        // 先断开现有连接
        disconnectInternal();

        if (group == null) {
            group = new NioEventLoopGroup(1);
        }

        try {
            Bootstrap bootstrap = new Bootstrap();
            bootstrap.group(group)
                    .channel(NioSocketChannel.class)
                    .option(ChannelOption.TCP_NODELAY, true)
                    .option(ChannelOption.SO_KEEPALIVE, true)
                    .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, nettyProperties.getConnectTimeout())
                    .handler(channelInitializer);

            ChannelFuture future = bootstrap.connect(host, port).sync();
            Channel channel = future.channel();

            if (future.isSuccess()) {
                channelRef.set(channel);
                connected = true;
                currentRouterId = centerId;
                currentRouterHost = host;
                currentRouterPort = port;
                reconnectionManager.reset();

                // 添加连接关闭监听器
                channel.closeFuture().addListener(closeFuture -> {
                    LOGGER.info("与路由节点 {} 的连接已断开", currentRouterId);
                    connected = false;
                    currentRouterId = null;
                    // 触发重连
                    if (!shutdown) {
                        handleConnectionFailure();
                    }
                });

                LOGGER.info("成功连接到路由节点 {} ({}:{})", centerId, host, port);
                return true;
            } else {
                LOGGER.error("连接路由节点失败");
                handleConnectionFailure();
                return false;
            }
        } catch (Exception e) {
            LOGGER.error("连接路由节点时出错: {}", e.getMessage());
            handleConnectionFailure();
            return false;
        }
    }

    /**
     * 启动服务发现定期刷新
     */
    private void startDiscoveryRefresh() {
        discoveryScheduler = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "discovery-refresh");
            t.setDaemon(true);
            return t;
        });

        discoveryScheduler.scheduleAtFixedRate(() -> {
            try {
                if (routerServiceDiscovery != null) {
                    routerServiceDiscovery.refreshRouterInstances();
                }
            } catch (Exception e) {
                LOGGER.debug("刷新路由节点列表失败: {}", e.getMessage());
            }
        }, connectionProperties.getDiscoveryRefreshIntervalMs(),
           connectionProperties.getDiscoveryRefreshIntervalMs(),
           TimeUnit.MILLISECONDS);
    }

    /**
     * 连接到路由节点（使用服务发现或配置）
     */
    @Override
    public synchronized void connect() {
        if (shutdown) {
            return;
        }

        if (connectionProperties.isDiscoveryEnabled() && routerServiceDiscovery != null) {
            // 使用服务发现
            List<ServiceInstance<Map<String, String>>> instances = routerServiceDiscovery.getRouterInstances();
            if (!instances.isEmpty()) {
                connectToAvailableRouter(instances);
            } else {
                // 服务发现没有找到路由节点，等待或使用配置
                LOGGER.info("服务发现未找到路由节点，稍后重试");
                scheduleReconnect();
            }
        } else {
            // 使用配置文件中的地址
            connectTo(nettyProperties.getHost(), nettyProperties.getPort(), null);
        }
    }

    /**
     * 处理连接失败
     */
    private void handleConnectionFailure() {
        connected = false;
        currentRouterId = null;
        scheduleReconnect();
    }

    /**
     * 安排重连
     */
    private void scheduleReconnect() {
        if (shutdown) {
            return;
        }
        reconnectionManager.scheduleReconnection(this::connect);
    }

    /**
     * 内部断开连接
     */
    private void disconnectInternal() {
        Channel channel = channelRef.getAndSet(null);
        if (channel != null && channel.isActive()) {
            channel.close();
        }
        connected = false;
    }

    @Override
    @PreDestroy
    public synchronized void disconnect() {
        LOGGER.info("正在断开 Netty 客户端...");
        shutdown = true;

        // 停止服务发现刷新
        if (discoveryScheduler != null) {
            discoveryScheduler.shutdown();
        }

        // 移除监听器
        if (routerServiceDiscovery != null) {
            routerServiceDiscovery.removeChangeListener(this::handleRouterInstancesChange);
        }

        connected = false;
        reconnectionManager.cancel();

        disconnectInternal();

        if (group != null) {
            group.shutdownGracefully();
        }

        LOGGER.info("Netty 客户端已断开");
    }

    @Override
    public boolean isConnected() {
        Channel channel = channelRef.get();
        return connected && channel != null && channel.isActive();
    }

    @Override
    public Channel getChannel() {
        return channelRef.get();
    }

    @Override
    public void sendMessage(Object message) {
        Channel channel = channelRef.get();
        if (isConnected() && channel != null) {
            channel.writeAndFlush(message);
        } else {
            LOGGER.warn("无法发送消息，客户端未连接");
        }
    }

    /**
     * 获取当前连接的路由节点ID
     */
    @Override
    public String getCurrentRouterId() {
        return currentRouterId;
    }

    /**
     * 获取当前连接的路由节点地址
     */
    @Override
    public String getCurrentRouterAddress() {
        if (currentRouterHost != null) {
            return currentRouterHost + ":" + currentRouterPort;
        }
        return null;
    }
}
