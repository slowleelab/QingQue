package com.example.customerserver.zookeeper;

import com.example.customerserver.config.ZookeeperProperties;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import org.apache.curator.framework.CuratorFramework;
import org.apache.curator.framework.CuratorFrameworkFactory;
import org.apache.curator.retry.ExponentialBackoffRetry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 坐席绑定关系查询器
 * 从 ZooKeeper 查询坐席与坐席后台节点的绑定关系
 * 用于跨节点消息路由
 *
 * ZK 路径结构：
 * /star-connection/agent-bindings/{agentId} -> {backendId}
 */
@Component
public class AgentBindingQuery {
    private static final Logger LOGGER = LoggerFactory.getLogger(AgentBindingQuery.class);

    private static final String AGENT_BINDINGS_PATH = "/agent-bindings";

    private final ZookeeperProperties zookeeperProperties;

    private CuratorFramework curatorClient;

    /**
     * 本地缓存的坐席绑定关系
     */
    private final Map<String, String> bindingCache = new ConcurrentHashMap<>();

    @Autowired
    public AgentBindingQuery(ZookeeperProperties zookeeperProperties) {
        this.zookeeperProperties = zookeeperProperties;
    }

    @PostConstruct
    public void init() {
        LOGGER.info("初始化坐席绑定关系查询器...");

        try {
            curatorClient = CuratorFrameworkFactory.builder()
                    .connectString(zookeeperProperties.getConnectString())
                    .sessionTimeoutMs(zookeeperProperties.getSessionTimeout())
                    .connectionTimeoutMs(zookeeperProperties.getConnectionTimeout())
                    .retryPolicy(new ExponentialBackoffRetry(
                            zookeeperProperties.getBaseSleepTime(),
                            zookeeperProperties.getMaxRetries()))
                    .namespace(zookeeperProperties.getNamespace())
                    .build();

            curatorClient.start();

            if (curatorClient.blockUntilConnected(5, java.util.concurrent.TimeUnit.SECONDS)) {
                LOGGER.info("坐席绑定关系查询器初始化成功");
            } else {
                LOGGER.warn("连接 ZooKeeper 超时，将使用本地缓存");
            }
        } catch (Exception e) {
            LOGGER.warn("坐席绑定关系查询器初始化失败: {}. 将使用本地缓存。", e.getMessage());
        }
    }

    @PreDestroy
    public void destroy() {
        LOGGER.info("关闭坐席绑定关系查询器...");
        if (curatorClient != null) {
            curatorClient.close();
        }
        LOGGER.info("坐席绑定关系查询器已关闭");
    }

    /**
     * 查询坐席绑定的后台节点ID
     *
     * @param agentId 坐席ID
     * @return 后台节点ID，如果未找到返回 null
     */
    public String getBackendId(String agentId) {
        // 先查本地缓存
        if (bindingCache.containsKey(agentId)) {
            return bindingCache.get(agentId);
        }

        String path = AGENT_BINDINGS_PATH + "/" + agentId;

        try {
            if (curatorClient != null) {
                byte[] data = curatorClient.getData().forPath(path);
                String backendId = new String(data, StandardCharsets.UTF_8);
                // 更新本地缓存
                bindingCache.put(agentId, backendId);
                return backendId;
            }
        } catch (org.apache.zookeeper.KeeperException.NoNodeException e) {
            LOGGER.debug("坐席绑定关系不存在: agentId={}", agentId);
        } catch (Exception e) {
            LOGGER.warn("查询坐席绑定关系失败: agentId={}", agentId, e);
        }

        return null;
    }

    /**
     * 更新本地缓存的绑定关系
     * 当 Router 接收到坐席注册消息时调用
     *
     * @param agentId 坐席ID
     * @param backendId 后台节点ID
     */
    public void updateBinding(String agentId, String backendId) {
        bindingCache.put(agentId, backendId);
        LOGGER.debug("更新坐席绑定缓存: agentId={}, backendId={}", agentId, backendId);
    }

    /**
     * 移除本地缓存的绑定关系
     * 当坐席断开连接时调用
     *
     * @param agentId 坐席ID
     */
    public void removeBinding(String agentId) {
        bindingCache.remove(agentId);
        LOGGER.debug("移除坐席绑定缓存: agentId={}", agentId);
    }

    /**
     * 检查坐席是否在线
     *
     * @param agentId 坐席ID
     * @return 是否在线
     */
    public boolean isAgentOnline(String agentId) {
        return getBackendId(agentId) != null;
    }

    /**
     * 获取指定后台节点的所有坐席ID
     *
     * @param backendId 后台节点ID
     * @return 坐席ID列表
     */
    public List<String> getAgentsByBackend(String backendId) {
        java.util.List<String> agents = new java.util.ArrayList<>();

        // 从本地缓存查找
        for (Map.Entry<String, String> entry : bindingCache.entrySet()) {
            if (backendId.equals(entry.getValue())) {
                agents.add(entry.getKey());
            }
        }

        return agents;
    }

    /**
     * 获取所有坐席绑定关系
     */
    public Map<String, String> getAllBindings() {
        return new HashMap<>(bindingCache);
    }

    /**
     * 清空本地缓存
     */
    public void clearCache() {
        bindingCache.clear();
        LOGGER.info("坐席绑定缓存已清空");
    }

    /**
     * 从 ZK 刷新缓存
     */
    public void refreshCache() {
        try {
            if (curatorClient != null) {
                List<String> agentIds = curatorClient.getChildren().forPath(AGENT_BINDINGS_PATH);
                Map<String, String> newCache = new HashMap<>();

                for (String agentId : agentIds) {
                    String backendId = getBackendId(agentId);
                    if (backendId != null) {
                        newCache.put(agentId, backendId);
                    }
                }

                bindingCache.clear();
                bindingCache.putAll(newCache);
                LOGGER.info("坐席绑定缓存已刷新，共 {} 条记录", bindingCache.size());
            }
        } catch (Exception e) {
            LOGGER.error("刷新坐席绑定缓存失败", e);
        }
    }
}
