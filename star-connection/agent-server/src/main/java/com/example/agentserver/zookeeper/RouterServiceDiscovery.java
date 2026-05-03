package com.example.agentserver.zookeeper;

import com.example.agentserver.config.ClientConnectionProperties;
import com.example.agentserver.config.ZookeeperProperties;
import org.apache.curator.framework.CuratorFramework;
import org.apache.curator.framework.CuratorFrameworkFactory;
import org.apache.curator.framework.state.ConnectionState;
import org.apache.curator.retry.ExponentialBackoffRetry;
import org.apache.curator.x.discovery.ServiceDiscovery;
import org.apache.curator.x.discovery.ServiceDiscoveryBuilder;
import org.apache.curator.x.discovery.ServiceInstance;
import org.apache.curator.x.discovery.details.JsonInstanceSerializer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

/**
 * 路由节点服务发现组件
 * 从 ZooKeeper 发现路由节点，监听变化
 */
@Component
public class RouterServiceDiscovery {
    private static final Logger LOGGER = LoggerFactory.getLogger(RouterServiceDiscovery.class);

    private final ZookeeperProperties zookeeperProperties;
    private final ClientConnectionProperties connectionProperties;

    private CuratorFramework client;
    private ServiceDiscovery<Map<String, String>> serviceDiscovery;

    // 当前可用的路由节点列表
    private final CopyOnWriteArrayList<ServiceInstance<Map<String, String>>> routerInstances = new CopyOnWriteArrayList<>();

    // 服务变化监听器
    private final List<Consumer<List<ServiceInstance<Map<String, String>>>>> changeListeners = new ArrayList<>();

    @Autowired
    public RouterServiceDiscovery(ZookeeperProperties zookeeperProperties,
                                   ClientConnectionProperties connectionProperties) {
        this.zookeeperProperties = zookeeperProperties;
        this.connectionProperties = connectionProperties;
    }

    @PostConstruct
    public void init() {
        if (!connectionProperties.isDiscoveryEnabled()) {
            LOGGER.info("路由节点服务发现已禁用，将使用配置文件中的地址");
            return;
        }

        LOGGER.info("正在初始化路由节点服务发现...");

        try {
            // 创建 Curator 客户端
            client = CuratorFrameworkFactory.builder()
                    .connectString(zookeeperProperties.getConnectString())
                    .sessionTimeoutMs(zookeeperProperties.getSessionTimeout())
                    .connectionTimeoutMs(zookeeperProperties.getConnectionTimeout())
                    .retryPolicy(new ExponentialBackoffRetry(
                            zookeeperProperties.getBaseSleepTime(),
                            zookeeperProperties.getMaxRetries()))
                    .namespace(zookeeperProperties.getNamespace())
                    .build();

            client.start();
            if (!client.blockUntilConnected(5, TimeUnit.SECONDS)) {
                LOGGER.warn("连接 ZooKeeper 超时，服务发现将不可用");
                return;
            }

            // 监听连接状态变化
            client.getConnectionStateListenable().addListener((client, newState) -> {
                if (newState == ConnectionState.RECONNECTED) {
                    LOGGER.info("ZooKeeper 重新连接，刷新路由节点列表");
                    refreshRouterInstances();
                }
            });

            // 创建服务发现
            @SuppressWarnings({"rawtypes", "unchecked"})
            JsonInstanceSerializer serializer = new JsonInstanceSerializer(Map.class);

            @SuppressWarnings("rawtypes")
            ServiceDiscovery discovery = ServiceDiscoveryBuilder.builder(Map.class)
                    .client(client)
                    .basePath(zookeeperProperties.getServicePath())
                    .serializer(serializer)
                    .build();

            serviceDiscovery = (ServiceDiscovery<Map<String, String>>) discovery;
            serviceDiscovery.start();

            // 初始发现路由节点
            refreshRouterInstances();

            LOGGER.info("路由节点服务发现初始化成功，发现 {} 个路由节点", routerInstances.size());
        } catch (Exception e) {
            LOGGER.warn("路由节点服务发现初始化失败: {}. 客户端将尝试使用配置文件中的地址。", e.getMessage());
            LOGGER.debug("服务发现初始化错误详情", e);
        }
    }

    @PreDestroy
    public void destroy() {
        LOGGER.info("正在关闭路由节点服务发现...");

        try {
            if (serviceDiscovery != null) {
                serviceDiscovery.close();
            }
        } catch (Exception e) {
            LOGGER.error("关闭服务发现时出错", e);
        }

        try {
            if (client != null) {
                client.close();
            }
        } catch (Exception e) {
            LOGGER.error("关闭 ZooKeeper 客户端时出错", e);
        }

        LOGGER.info("路由节点服务发现已关闭");
    }

    /**
     * 刷新路由节点实例列表
     */
    public synchronized void refreshRouterInstances() {
        if (serviceDiscovery == null) {
            return;
        }

        try {
            List<ServiceInstance<Map<String, String>>> newInstances = new ArrayList<>();

            // 获取指定名称的路由节点服务
            Collection<ServiceInstance<Map<String, String>>> instances =
                    serviceDiscovery.queryForInstances(connectionProperties.getRouterServiceName());

            for (ServiceInstance<Map<String, String>> instance : instances) {
                // 过滤只包含路由节点
                Map<String, String> payload = instance.getPayload();
                if (payload != null && "router".equals(payload.get("service-type"))) {
                    newInstances.add(instance);
                    LOGGER.debug("发现路由节点: {} ({}:{})",
                            instance.getId(), instance.getAddress(), instance.getPort());
                }
            }

            // 检查是否有变化
            boolean changed = routerInstances.size() != newInstances.size() ||
                    !routerInstances.containsAll(newInstances) ||
                    !newInstances.containsAll(routerInstances);

            if (changed) {
                routerInstances.clear();
                routerInstances.addAll(newInstances);
                LOGGER.info("路由节点列表已更新，当前有 {} 个路由节点", routerInstances.size());

                // 通知所有监听器
                notifyChangeListeners();
            }
        } catch (Exception e) {
            LOGGER.error("刷新路由节点实例列表失败", e);
        }
    }

    /**
     * 获取可用的路由节点列表
     */
    public List<ServiceInstance<Map<String, String>>> getRouterInstances() {
        return Collections.unmodifiableList(routerInstances);
    }

    /**
     * 获取一个可用的路由节点（轮询或随机）
     */
    public ServiceInstance<Map<String, String>> getOneRouterInstance() {
        if (routerInstances.isEmpty()) {
            return null;
        }
        // 简单返回第一个，可以扩展为负载均衡策略
        return routerInstances.get(0);
    }

    /**
     * 检查是否有可用的路由节点
     */
    public boolean hasRouterInstances() {
        return !routerInstances.isEmpty();
    }

    /**
     * 检查 ZooKeeper 是否已连接
     */
    public boolean isConnected() {
        return client != null && client.getZookeeperClient().isConnected();
    }

    /**
     * 添加服务变化监听器
     */
    public void addChangeListener(Consumer<List<ServiceInstance<Map<String, String>>>> listener) {
        changeListeners.add(listener);
        // 立即通知当前状态
        if (!routerInstances.isEmpty()) {
            listener.accept(getRouterInstances());
        }
    }

    /**
     * 移除服务变化监听器
     */
    public void removeChangeListener(Consumer<List<ServiceInstance<Map<String, String>>>> listener) {
        changeListeners.remove(listener);
    }

    /**
     * 通知所有监听器
     */
    private void notifyChangeListeners() {
        List<ServiceInstance<Map<String, String>>> instances = getRouterInstances();
        for (Consumer<List<ServiceInstance<Map<String, String>>>> listener : changeListeners) {
            try {
                listener.accept(instances);
            } catch (Exception e) {
                LOGGER.error("通知服务变化监听器失败", e);
            }
        }
    }
}
