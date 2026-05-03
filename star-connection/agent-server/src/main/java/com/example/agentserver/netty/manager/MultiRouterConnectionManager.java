package com.example.agentserver.netty.manager;

import com.example.agentserver.config.ClientConnectionProperties;
import com.example.agentserver.config.NettyAgentServerProperties;
import com.example.agentserver.netty.ClientChannelInitializer;
import com.example.agentserver.zookeeper.ClientConnectionRegistry;
import com.example.agentserver.zookeeper.RouterServiceDiscovery;
import io.netty.bootstrap.Bootstrap;
import io.netty.channel.Channel;
import io.netty.channel.ChannelFuture;
import io.netty.channel.ChannelOption;
import io.netty.channel.EventLoopGroup;
import io.netty.channel.nio.NioEventLoopGroup;
import io.netty.channel.socket.nio.NioSocketChannel;
import org.apache.curator.x.discovery.ServiceInstance;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 多路由连接管理器
 * 管理到所有路由节点的连接，支持集群模式
 */
@Component
@ConditionalOnProperty(name = "client.connection.connect-to-all-routers", havingValue = "true")
public class MultiRouterConnectionManager implements ConnectionManager {
    private static final Logger LOGGER = LoggerFactory.getLogger(MultiRouterConnectionManager.class);

    private final NettyAgentServerProperties nettyProperties;
    private final ClientConnectionProperties connectionProperties;
    private final RouterServiceDiscovery routerServiceDiscovery;
    private final ClientConnectionRegistry connectionRegistry;
    private final ReconnectionManager reconnectionManager;
    private final ClientChannelInitializer channelInitializer;

    private EventLoopGroup group;
    private final Map<String, Channel> routerChannels = new ConcurrentHashMap<>();
    private final Map<String, String> channelToRouter = new ConcurrentHashMap<>();
    private volatile boolean shutdown = false;
    private ScheduledExecutorService discoveryScheduler;
    private final AtomicInteger roundRobinIndex = new AtomicInteger(0);

    public MultiRouterConnectionManager(NettyAgentServerProperties nettyProperties,
                                        ClientConnectionProperties connectionProperties,
                                        RouterServiceDiscovery routerServiceDiscovery,
                                        ClientConnectionRegistry connectionRegistry,
                                        ReconnectionManager reconnectionManager,
                                        ClientChannelInitializer channelInitializer) {
        this.nettyProperties = nettyProperties;
        this.connectionProperties = connectionProperties;
        this.routerServiceDiscovery = routerServiceDiscovery;
        this.connectionRegistry = connectionRegistry;
        this.reconnectionManager = reconnectionManager;
        this.channelInitializer = channelInitializer;
    }

    @PostConstruct
    public void init() {
        LOGGER.info("正在初始化多路由连接管理器...");

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
            return;
        }

        // 检查现有连接
        for (ServiceInstance<Map<String, String>> instance : instances) {
            String routerId = instance.getId();
            if (!routerChannels.containsKey(routerId)) {
                // 新增的路由节点，尝试连接
                connectToRouter(instance);
            }
        }

        // 移除已下线的路由节点连接
        routerChannels.keySet().removeIf(routerId -> {
            boolean stillExists = instances.stream()
                    .anyMatch(i -> i.getId().equals(routerId));
            if (!stillExists) {
                LOGGER.info("路由节点 {} 已下线，关闭连接", routerId);
                closeRouterChannel(routerId);
                return true;
            }
            return false;
        });
    }

    /**
     * 连接到指定的路由节点
     */
    private boolean connectToRouter(ServiceInstance<Map<String, String>> instance) {
        String routerId = instance.getId();
        String host = instance.getAddress();
        int port = instance.getPort();

        if (routerChannels.containsKey(routerId)) {
            Channel existing = routerChannels.get(routerId);
            if (existing != null && existing.isActive()) {
                LOGGER.debug("已经连接到路由节点 {}", routerId);
                return true;
            }
        }

        LOGGER.info("正在连接到路由节点 {} ({}:{})...", routerId, host, port);

        if (group == null) {
            group = new NioEventLoopGroup(2);
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
                routerChannels.put(routerId, channel);
                channelToRouter.put(channel.id().asShortText(), routerId);

                channel.closeFuture().addListener(closeFuture -> {
                    LOGGER.info("与路由节点 {} 的连接已断开", routerId);
                    routerChannels.remove(routerId);
                    channelToRouter.remove(channel.id().asShortText());
                });

                LOGGER.info("成功连接到路由节点 {} ({}:{})", routerId, host, port);
                return true;
            }
        } catch (Exception e) {
            LOGGER.error("连接路由节点 {} 失败: {}", routerId, e.getMessage());
        }
        return false;
    }

    /**
     * 关闭指定路由节点的连接
     */
    private void closeRouterChannel(String routerId) {
        Channel channel = routerChannels.remove(routerId);
        if (channel != null && channel.isActive()) {
            channel.close();
        }
    }

    /**
     * 连接到所有可用的路由节点
     */
    @Override
    public synchronized void connect() {
        if (shutdown) {
            return;
        }

        if (connectionProperties.isDiscoveryEnabled() && routerServiceDiscovery != null) {
            List<ServiceInstance<Map<String, String>>> instances = routerServiceDiscovery.getRouterInstances();
            if (!instances.isEmpty()) {
                for (ServiceInstance<Map<String, String>> instance : instances) {
                    connectToRouter(instance);
                }
            } else {
                LOGGER.info("服务发现未找到路由节点，稍后重试");
                scheduleReconnect();
            }
        } else {
            // 单路由模式，使用配置文件中的地址
            connectToSingleRouter();
        }
    }

    /**
     * 单路由模式连接
     */
    private void connectToSingleRouter() {
        try {
            if (group == null) {
                group = new NioEventLoopGroup(1);
            }

            Bootstrap bootstrap = new Bootstrap();
            bootstrap.group(group)
                    .channel(NioSocketChannel.class)
                    .option(ChannelOption.TCP_NODELAY, true)
                    .option(ChannelOption.SO_KEEPALIVE, true)
                    .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, nettyProperties.getConnectTimeout())
                    .handler(channelInitializer);

            ChannelFuture future = bootstrap.connect(
                    nettyProperties.getHost(),
                    nettyProperties.getPort()
            ).sync();

            if (future.isSuccess()) {
                Channel channel = future.channel();
                String channelId = channel.id().asShortText();
                routerChannels.put("default", channel);
                channelToRouter.put(channelId, "default");
                LOGGER.info("成功连接到路由节点 {}:{}",
                        nettyProperties.getHost(), nettyProperties.getPort());
            }
        } catch (Exception e) {
            LOGGER.error("连接路由节点失败: {}", e.getMessage());
            scheduleReconnect();
        }
    }

    /**
     * 启动服务发现定期刷新
     */
    private void startDiscoveryRefresh() {
        discoveryScheduler = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "router-discovery-refresh");
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
     * 安排重连
     */
    private void scheduleReconnect() {
        if (shutdown) {
            return;
        }
        reconnectionManager.scheduleReconnection(this::connect);
    }

    /**
     * 发送消息到指定路由节点
     */
    public boolean sendToRouter(String routerId, Object message) {
        Channel channel = routerChannels.get(routerId);
        if (channel != null && channel.isActive()) {
            channel.writeAndFlush(message);
            return true;
        }
        return false;
    }

    /**
     * 发送消息，自动选择路由节点
     * 优先选择目标客户端已连接的路由节点
     */
    @Override
    public boolean sendMessage(String targetServiceId, Object message) {
        // 1. 查询目标客户端连接的路由节点
        Optional<String> targetRouterId = connectionRegistry.getConnectionRouterId(targetServiceId);

        if (targetRouterId.isPresent()) {
            String routerId = targetRouterId.get();
            // 2. 尝试发送到目标路由节点
            if (sendToRouter(routerId, message)) {
                LOGGER.debug("消息发送到目标路由节点 {} (目标: {})", routerId, targetServiceId);
                return true;
            }
        }

        // 3. 目标路由节点不可用，尝试所有路由节点
        for (Map.Entry<String, Channel> entry : routerChannels.entrySet()) {
            Channel channel = entry.getValue();
            if (channel.isActive()) {
                channel.writeAndFlush(message);
                LOGGER.debug("消息发送到路由节点 {} (目标: {})", entry.getKey(), targetServiceId);
                return true;
            }
        }

        LOGGER.warn("没有可用的路由节点，无法发送消息到 {}", targetServiceId);
        return false;
    }

    /**
     * 轮询选择一个路由节点发送消息
     */
    public boolean sendMessageRoundRobin(Object message) {
        if (routerChannels.isEmpty()) {
            LOGGER.warn("没有可用的路由节点");
            return false;
        }

        // 简单轮询
        String[] routerIds = routerChannels.keySet().toArray(new String[0]);
        int index = roundRobinIndex.getAndIncrement() % routerIds.length;
        String routerId = routerIds[index];

        return sendToRouter(routerId, message);
    }

    /**
     * 检查是否已连接到任意路由节点
     */
    @Override
    public boolean isConnected() {
        return routerChannels.values().stream().anyMatch(Channel::isActive);
    }

    /**
     * 获取已连接的路由节点数量
     */
    @Override
    public int getConnectedRouterCount() {
        return (int) routerChannels.values().stream().filter(Channel::isActive).count();
    }

    /**
     * 获取已连接的路由节点ID列表
     */
    @Override
    public Set<String> getConnectedRouterIds() {
        return routerChannels.keySet();
    }

    @Override
    @PreDestroy
    public void disconnect() {
        LOGGER.info("正在断开多路由连接管理器...");
        shutdown = true;

        if (discoveryScheduler != null) {
            discoveryScheduler.shutdown();
        }

        if (routerServiceDiscovery != null) {
            routerServiceDiscovery.removeChangeListener(this::handleRouterInstancesChange);
        }

        routerChannels.clear();
        channelToRouter.clear();

        if (group != null) {
            group.shutdownGracefully();
        }

        LOGGER.info("多路由连接管理器已关闭");
    }

    @Override
    public Channel getChannel() {
        // 多路由模式下返回任意一个活跃的通道
        return routerChannels.values().stream()
                .filter(Channel::isActive)
                .findFirst()
                .orElse(null);
    }

    @Override
    public void sendMessage(Object message) {
        // 默认使用轮询方式发送
        sendMessageRoundRobin(message);
    }

    @Override
    public String getCurrentRouterId() {
        // 多路由模式下返回第一个活跃的路由节点ID
        return routerChannels.entrySet().stream()
                .filter(e -> e.getValue().isActive())
                .map(Map.Entry::getKey)
                .findFirst()
                .orElse(null);
    }

    @Override
    public String getCurrentRouterAddress() {
        // 多路由模式下返回第一个活跃通道的地址
        Channel channel = getChannel();
        if (channel != null && channel.remoteAddress() != null) {
            return channel.remoteAddress().toString();
        }
        return null;
    }
}
