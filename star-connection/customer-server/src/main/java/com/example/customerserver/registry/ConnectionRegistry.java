package com.example.customerserver.registry;

import com.example.customerserver.config.CustomerServerProperties;
import com.example.customerserver.config.ZookeeperProperties;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.curator.framework.CuratorFramework;
import org.apache.curator.framework.CuratorFrameworkFactory;
import org.apache.curator.retry.ExponentialBackoffRetry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.TimeUnit;

/**
 * 连接注册表
 * 将客户端连接信息注册到 ZooKeeper，支持集群部署
 */
@Component
public class ConnectionRegistry {
    private static final Logger LOGGER = LoggerFactory.getLogger(ConnectionRegistry.class);

    private final ZookeeperProperties zookeeperProperties;
    private final CustomerServerProperties routerProperties;
    private final ObjectMapper objectMapper = new ObjectMapper();

    private CuratorFramework client;
    private String connectionPath;

    @Autowired
    public ConnectionRegistry(ZookeeperProperties zookeeperProperties,
                               CustomerServerProperties routerProperties) {
        this.zookeeperProperties = zookeeperProperties;
        this.routerProperties = routerProperties;
    }

    @PostConstruct
    public void init() {
        if (!routerProperties.isRegisterToZookeeper()) {
            LOGGER.info("连接注册已禁用");
            return;
        }

        LOGGER.info("正在初始化连接注册表...");

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
                LOGGER.warn("连接 ZooKeeper 超时，连接注册将不可用");
                return;
            }

            // 设置连接注册路径
            connectionPath = "/connections";

            // 确保连接路径存在
            ensurePathExists(connectionPath);

            LOGGER.info("连接注册表初始化成功，路径: {}", connectionPath);
        } catch (Exception e) {
            LOGGER.warn("连接注册表初始化失败: {}", e.getMessage());
            LOGGER.debug("初始化错误详情", e);
        }
    }

    /**
     * 确保路径存在
     */
    private void ensurePathExists(String path) throws Exception {
        if (client.checkExists().forPath(path) == null) {
            client.create().creatingParentsIfNeeded().forPath(path);
            LOGGER.debug("创建路径: {}", path);
        }
    }

    /**
     * 注册客户端连接
     * @param serviceId 客户端服务ID
     */
    public void registerConnection(String serviceId) {
        if (client == null) {
            return;
        }

        try {
            String path = connectionPath + "/" + serviceId;

            Map<String, String> data = new HashMap<>();
            data.put("routerId", routerProperties.getServiceId());
            data.put("timestamp", String.valueOf(System.currentTimeMillis()));

            byte[] bytes = objectMapper.writeValueAsBytes(data);

            if (client.checkExists().forPath(path) == null) {
                // 创建临时节点，客户端断开时自动删除
                client.create()
                        .withMode(org.apache.zookeeper.CreateMode.EPHEMERAL)
                        .forPath(path, bytes);
                LOGGER.debug("注册客户端连接: {} -> {}", serviceId, routerProperties.getServiceId());
            } else {
                // 更新数据
                client.setData().forPath(path, bytes);
                LOGGER.debug("更新客户端连接: {} -> {}", serviceId, routerProperties.getServiceId());
            }
        } catch (Exception e) {
            LOGGER.error("注册客户端连接失败: {}", serviceId, e);
        }
    }

    /**
     * 注销客户端连接
     * @param serviceId 客户端服务ID
     */
    public void unregisterConnection(String serviceId) {
        if (client == null) {
            return;
        }

        try {
            String path = connectionPath + "/" + serviceId;

            if (client.checkExists().forPath(path) != null) {
                client.delete().forPath(path);
                LOGGER.debug("注销客户端连接: {}", serviceId);
            }
        } catch (Exception e) {
            LOGGER.error("注销客户端连接失败: {}", serviceId, e);
        }
    }

    /**
     * 查询客户端连接所在的路由节点
     * @param serviceId 客户端服务ID
     * @return 路由节点ID，如果未找到返回 null
     */
    public String getConnectionRouter(String serviceId) {
        if (client == null) {
            return null;
        }

        try {
            String path = connectionPath + "/" + serviceId;

            if (client.checkExists().forPath(path) != null) {
                byte[] data = client.getData().forPath(path);
                @SuppressWarnings("unchecked")
                Map<String, String> map = objectMapper.readValue(data, Map.class);
                return map.get("routerId");
            }
        } catch (Exception e) {
            LOGGER.debug("查询客户端连接失败: {}", serviceId);
        }
        return null;
    }

    /**
     * 检查客户端是否已注册
     * @param serviceId 客户端服务ID
     * @return 是否已注册
     */
    public boolean isConnectionRegistered(String serviceId) {
        if (client == null) {
            return false;
        }

        try {
            String path = connectionPath + "/" + serviceId;
            return client.checkExists().forPath(path) != null;
        } catch (Exception e) {
            return false;
        }
    }

    /**
     * 检查 ZooKeeper 是否已连接
     */
    public boolean isConnected() {
        return client != null && client.getZookeeperClient().isConnected();
    }

    @PreDestroy
    public void destroy() {
        LOGGER.info("正在关闭连接注册表...");

        try {
            if (client != null) {
                client.close();
            }
        } catch (Exception e) {
            LOGGER.error("关闭连接注册表时出错", e);
        }

        LOGGER.info("连接注册表已关闭");
    }
}
