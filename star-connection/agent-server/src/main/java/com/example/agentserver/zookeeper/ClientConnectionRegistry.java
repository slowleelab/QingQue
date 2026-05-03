package com.example.agentserver.zookeeper;

import com.example.agentserver.config.ZookeeperProperties;
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
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.TimeUnit;

/**
 * 客户端连接注册表查询组件
 * 用于查询其他客户端连接到哪个路由节点
 */
@Component
public class ClientConnectionRegistry {
    private static final Logger LOGGER = LoggerFactory.getLogger(ClientConnectionRegistry.class);

    private final ZookeeperProperties zookeeperProperties;
    private final ObjectMapper objectMapper = new ObjectMapper();

    private CuratorFramework client;
    private String connectionPath;

    @Autowired
    public ClientConnectionRegistry(ZookeeperProperties zookeeperProperties) {
        this.zookeeperProperties = zookeeperProperties;
    }

    @PostConstruct
    public void init() {
        LOGGER.info("正在初始化客户端连接注册表...");

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
                LOGGER.warn("连接 ZooKeeper 超时，连接注册表查询将不可用");
                return;
            }

            connectionPath = "/connections";
            LOGGER.info("客户端连接注册表初始化成功");
        } catch (Exception e) {
            LOGGER.warn("客户端连接注册表初始化失败: {}", e.getMessage());
            LOGGER.debug("初始化错误详情", e);
        }
    }

    /**
     * 查询客户端连接所在的路由节点
     * @param serviceId 客户端服务ID
     * @return 路由节点ID，如果未找到返回空
     */
    public Optional<String> getConnectionRouterId(String serviceId) {
        if (client == null) {
            return Optional.empty();
        }

        try {
            String path = connectionPath + "/" + serviceId;

            if (client.checkExists().forPath(path) != null) {
                byte[] data = client.getData().forPath(path);
                @SuppressWarnings("unchecked")
                Map<String, String> map = objectMapper.readValue(data, Map.class);
                String routerId = map.get("routerId");
                return Optional.ofNullable(routerId);
            }
        } catch (Exception e) {
            LOGGER.debug("查询客户端连接失败: {}", serviceId);
        }
        return Optional.empty();
    }

    /**
     * 检查客户端是否已注册
     * @param serviceId 客户端服务ID
     * @return 是否已注册
     */
    public boolean isClientRegistered(String serviceId) {
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
        LOGGER.info("正在关闭客户端连接注册表...");

        try {
            if (client != null) {
                client.close();
            }
        } catch (Exception e) {
            LOGGER.error("关闭客户端连接注册表时出错", e);
        }

        LOGGER.info("客户端连接注册表已关闭");
    }
}
