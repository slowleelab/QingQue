package com.example.agentserver.zookeeper;

import com.example.agentserver.config.AgentServerProperties;
import com.example.agentserver.config.ZookeeperProperties;
import org.apache.curator.framework.CuratorFramework;
import org.apache.curator.framework.recipes.cache.CuratorCache;
import org.apache.zookeeper.CreateMode;
import org.apache.zookeeper.KeeperException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 坐席绑定关系注册器
 * 将坐席与坐席后台节点的绑定关系注册到 ZooKeeper
 * 用于跨节点的消息路由
 *
 * ZK 路径结构：
 * /star-connection/agent-bindings/{agentId} -> {backendId}
 */
@Component
public class AgentBindingRegistry {
    private static final Logger LOGGER = LoggerFactory.getLogger(AgentBindingRegistry.class);

    private static final String AGENT_BINDINGS_PATH = "/agent-bindings";

    private final ZookeeperProperties zookeeperProperties;
    private final AgentServerProperties clientProperties;

    private CuratorFramework curatorClient;
    private CuratorCache cache;

    /**
     * 本地缓存的坐席绑定关系
     */
    private final Map<String, String> localBindings = new ConcurrentHashMap<>();

    @Autowired
    public AgentBindingRegistry(ZookeeperProperties zookeeperProperties, AgentServerProperties clientProperties) {
        this.zookeeperProperties = zookeeperProperties;
        this.clientProperties = clientProperties;
    }

    @PostConstruct
    public void init() {
        LOGGER.info("初始化坐席绑定关系注册器...");

        try {
            // 获取现有的 CuratorFramework 客户端（从 ServiceRegistry 或直接创建）
            // 这里简化处理，直接创建客户端连接
            curatorClient = org.apache.curator.framework.CuratorFrameworkFactory.builder()
                    .connectString(zookeeperProperties.getConnectString())
                    .sessionTimeoutMs(zookeeperProperties.getSessionTimeout())
                    .connectionTimeoutMs(zookeeperProperties.getConnectionTimeout())
                    .retryPolicy(new org.apache.curator.retry.ExponentialBackoffRetry(
                            zookeeperProperties.getBaseSleepTime(),
                            zookeeperProperties.getMaxRetries()))
                    .namespace(zookeeperProperties.getNamespace())
                    .build();

            curatorClient.start();

            // 确保根路径存在
            ensurePathExists(AGENT_BINDINGS_PATH);

            // 启动缓存监听
            String fullPath = zookeeperProperties.getNamespace() + AGENT_BINDINGS_PATH;
            cache = CuratorCache.build(curatorClient, AGENT_BINDINGS_PATH);
            cache.start();

            LOGGER.info("坐席绑定关系注册器初始化成功，路径: {}", fullPath);
        } catch (Exception e) {
            LOGGER.warn("坐席绑定关系注册器初始化失败: {}. 功能将降级使用本地缓存。", e.getMessage());
        }
    }

    @PreDestroy
    public void destroy() {
        LOGGER.info("关闭坐席绑定关系注册器...");

        // 清理本节点注册的所有坐席绑定
        for (String agentId : localBindings.keySet()) {
            try {
                unregisterAgent(agentId);
            } catch (Exception e) {
                LOGGER.warn("注销坐席绑定失败: agentId={}", agentId, e);
            }
        }

        if (cache != null) {
            cache.close();
        }

        if (curatorClient != null) {
            curatorClient.close();
        }

        LOGGER.info("坐席绑定关系注册器已关闭");
    }

    /**
     * 注册坐席绑定关系
     *
     * @param agentId 坐席ID
     * @param backendId 坐席后台节点ID
     */
    public void registerAgentBinding(String agentId, String backendId) {
        String path = AGENT_BINDINGS_PATH + "/" + agentId;

        try {
            String data = backendId;

            if (curatorClient != null) {
                try {
                    // 尝试创建临时节点
                    curatorClient.create()
                            .creatingParentsIfNeeded()
                            .withMode(CreateMode.EPHEMERAL)
                            .forPath(path, data.getBytes(StandardCharsets.UTF_8));

                    LOGGER.info("坐席绑定关系已注册到 ZK: agentId={}, backendId={}", agentId, backendId);
                } catch (KeeperException.NodeExistsException e) {
                    // 节点已存在，更新数据
                    curatorClient.setData()
                            .forPath(path, data.getBytes(StandardCharsets.UTF_8));

                    LOGGER.info("坐席绑定关系已更新到 ZK: agentId={}, backendId={}", agentId, backendId);
                }
            }

            // 同时保存到本地缓存
            localBindings.put(agentId, backendId);

        } catch (Exception e) {
            LOGGER.error("注册坐席绑定关系到 ZK 失败: agentId={}, backendId={}", agentId, backendId, e);
            // 即使 ZK 失败，仍然保存到本地缓存
            localBindings.put(agentId, backendId);
        }
    }

    /**
     * 注销坐席绑定关系
     *
     * @param agentId 坐席ID
     */
    public void unregisterAgent(String agentId) {
        String path = AGENT_BINDINGS_PATH + "/" + agentId;

        try {
            if (curatorClient != null) {
                try {
                    curatorClient.delete().forPath(path);
                    LOGGER.info("坐席绑定关系已从 ZK 注销: agentId={}", agentId);
                } catch (KeeperException.NoNodeException e) {
                    LOGGER.debug("坐席绑定关系节点不存在: agentId={}", agentId);
                }
            }

            // 从本地缓存移除
            localBindings.remove(agentId);

        } catch (Exception e) {
            LOGGER.error("从 ZK 注销坐席绑定关系失败: agentId={}", agentId, e);
            // 确保本地缓存也移除
            localBindings.remove(agentId);
        }
    }

    /**
     * 查询坐席绑定的后台节点ID
     *
     * @param agentId 坐席ID
     * @return 后台节点ID，如果未找到返回 null
     */
    public String getBackendId(String agentId) {
        String path = AGENT_BINDINGS_PATH + "/" + agentId;

        try {
            // 先查本地缓存
            if (localBindings.containsKey(agentId)) {
                return localBindings.get(agentId);
            }

            // 再查 ZK
            if (curatorClient != null) {
                byte[] data = curatorClient.getData().forPath(path);
                return new String(data, StandardCharsets.UTF_8);
            }
        } catch (KeeperException.NoNodeException e) {
            LOGGER.debug("坐席绑定关系不存在: agentId={}", agentId);
        } catch (Exception e) {
            LOGGER.warn("查询坐席绑定关系失败: agentId={}", agentId, e);
        }

        return null;
    }

    /**
     * 检查坐席是否在线（是否有绑定关系）
     *
     * @param agentId 坐席ID
     * @return 是否在线
     */
    public boolean isAgentOnline(String agentId) {
        return getBackendId(agentId) != null;
    }

    /**
     * 获取本节点注册的所有坐席ID
     */
    public Map<String, String> getLocalBindings() {
        return new HashMap<>(localBindings);
    }

    /**
     * 获取指定后台节点的所有坐席ID
     *
     * @param backendId 后台节点ID
     * @return 坐席ID列表
     */
    public java.util.List<String> getAgentsByBackend(String backendId) {
        java.util.List<String> agents = new java.util.ArrayList<>();

        try {
            if (curatorClient != null) {
                // 遍历所有绑定关系
                for (String agentId : curatorClient.getChildren().forPath(AGENT_BINDINGS_PATH)) {
                    String bId = getBackendId(agentId);
                    if (backendId.equals(bId)) {
                        agents.add(agentId);
                    }
                }
            }
        } catch (Exception e) {
            LOGGER.warn("获取后台节点的坐席列表失败: backendId={}", backendId, e);
        }

        return agents;
    }

    /**
     * 获取所有坐席绑定关系
     */
    public Map<String, String> getAllBindings() {
        Map<String, String> bindings = new HashMap<>();

        try {
            if (curatorClient != null) {
                for (String agentId : curatorClient.getChildren().forPath(AGENT_BINDINGS_PATH)) {
                    String backendId = getBackendId(agentId);
                    if (backendId != null) {
                        bindings.put(agentId, backendId);
                    }
                }
            }
        } catch (Exception e) {
            LOGGER.warn("获取所有坐席绑定关系失败", e);
        }

        return bindings;
    }

    /**
     * 确保路径存在
     */
    private void ensurePathExists(String path) throws Exception {
        if (curatorClient != null) {
            try {
                curatorClient.create()
                        .creatingParentsIfNeeded()
                        .withMode(CreateMode.PERSISTENT)
                        .forPath(path);
            } catch (KeeperException.NodeExistsException e) {
                // 路径已存在
            }
        }
    }

    /**
     * 获取当前后台节点ID
     */
    public String getCurrentBackendId() {
        return clientProperties.getServiceId();
    }
}
