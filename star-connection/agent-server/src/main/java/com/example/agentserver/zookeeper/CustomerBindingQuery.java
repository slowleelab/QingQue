package com.example.agentserver.zookeeper;

import com.example.agentserver.config.ZookeeperProperties;
import com.google.common.cache.Cache;
import com.google.common.cache.CacheBuilder;
import org.apache.curator.framework.CuratorFramework;
import org.apache.curator.framework.CuratorFrameworkFactory;
import org.apache.curator.retry.ExponentialBackoffRetry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.TimeUnit;

/**
 * 客户绑定关系查询器
 *
 * <p>这是AB（坐席后台）端用于查询客户绑定关系的组件。
 * 当坐席发送消息给客户时，需要通过此查询器找到客户连接的CF节点。</p>
 *
 * <h3>架构位置：</h3>
 * <pre>
 *                      ┌─────────────────┐
 *                      │   ZooKeeper     │
 *                      │ /customer-bindings/{customerId} → routerId
 *                      └────────┬────────┘
 *                               │ 查询
 *                               ▼
 *                      ┌─────────────────┐
 *                      │ CustomerBinding │
 *                      │     Query       │
 *                      │   (AB端组件)    │
 *                      └────────┬────────┘
 *                               │ 使用routerId路由
 *                               ▼
 *                      ┌─────────────────┐
 *                      │ MultiRouterConn │
 *                      │ ectionManager   │
 *                      └─────────────────┘
 * </pre>
 *
 * <h3>查询策略（两级缓存）：</h3>
 * <pre>
 * ┌───────────────────────────────────────────────────────┐
 * │                    查询流程                            │
 * ├───────────────────────────────────────────────────────┤
 * │  1. 查询本地内存缓存 (Guava Cache, TTL 30秒)           │
 * │     ├─ 命中 → 直接返回 routerId                        │
 * │     └─ 未命中 ↓                                       │
 * │                                                       │
 * │  2. 查询 ZooKeeper                                    │
 * │     ├─ 找到 → 写入本地缓存，返回 routerId              │
 * │     └─ 未找到 → 返回 empty                            │
 * └───────────────────────────────────────────────────────┘
 * </pre>
 *
 * <h3>性能优化：</h3>
 * <p>对于千万级消息量的系统，本地缓存至关重要：</p>
 * <ul>
 *   <li>假设每秒3000条坐席回复消息</li>
 *   <li>95%缓存命中率 = 2850次本地内存查询 + 150次ZK查询</li>
 *   <li>无缓存 = 3000次ZK查询（ZK将成为瓶颈）</li>
 * </ul>
 *
 * <h3>缓存一致性：</h3>
 * <ol>
 *   <li><b>TTL过期</b>：30秒后自动刷新</li>
 *   <li><b>发送失败失效</b>：消息发送失败时调用{@link #invalidate(String)}</li>
 *   <li><b>会话映射</b>：维护sessionId → customerId映射，便于快速查询</li>
 * </ol>
 *
 * @author Customer Service Platform Team
 * @version 1.0.0
 * @see com.example.customerserver.zookeeper.CustomerBindingRegistry
 * @see com.example.agentserver.agent.AgentManager#sendChatMessageToRouter
 */
@Component
public class CustomerBindingQuery {

    private static final Logger LOGGER = LoggerFactory.getLogger(CustomerBindingQuery.class);

    /**
     * ZK客户绑定节点路径
     */
    private static final String CUSTOMER_BINDINGS_PATH = "/customer-bindings";

    private final ZookeeperProperties zookeeperProperties;

    /**
     * Curator客户端实例
     */
    private CuratorFramework curatorClient;

    /**
     * 本地缓存（TTL 30秒）
     * <p>存储 customerId → routerId 的映射关系</p>
     * <p>使用Guava Cache的原因：</p>
     * <ul>
     *   <li>线程安全</li>
     *   <li>支持TTL自动过期</li>
     *   <li>支持容量限制</li>
     *   <li>高性能</li>
     * </ul>
     */
    private final Cache<String, String> bindingCache;

    /**
     * Session存储：sessionId → customerId 映射
     * <p>用于通过sessionId快速查找customerId，进而查询routerId</p>
     * <p>在收到SESSION_ASSIGN消息时注册</p>
     */
    private final Map<String, String> sessionCustomerMap = new java.util.concurrent.ConcurrentHashMap<>();

    @Autowired
    public CustomerBindingQuery(ZookeeperProperties zookeeperProperties) {
        this.zookeeperProperties = zookeeperProperties;
        // TTL缓存，30秒过期，最大10万条
        this.bindingCache = CacheBuilder.newBuilder()
                .expireAfterWrite(30, TimeUnit.SECONDS)
                .maximumSize(100000)
                .build();
    }

    /**
     * 初始化方法
     * <p>Spring容器启动后自动调用</p>
     */
    @PostConstruct
    public void init() {
        LOGGER.info("初始化客户绑定关系查询器...");

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

            if (curatorClient.blockUntilConnected(5, TimeUnit.SECONDS)) {
                LOGGER.info("客户绑定关系查询器初始化成功");
            } else {
                LOGGER.warn("连接 ZooKeeper 超时，将使用本地缓存");
            }
        } catch (Exception e) {
            LOGGER.warn("客户绑定关系查询器初始化失败: {}. 将使用本地缓存。", e.getMessage());
            LOGGER.debug("初始化错误详情", e);
        }
    }

    /**
     * 销毁方法
     */
    @PreDestroy
    public void destroy() {
        LOGGER.info("关闭客户绑定关系查询器...");
        if (curatorClient != null) {
            curatorClient.close();
        }
        LOGGER.info("客户绑定关系查询器已关闭");
    }

    /**
     * 查询客户绑定的路由节点ID
     *
     * <p>这是核心查询方法，按以下优先级查询：</p>
     * <ol>
     *   <li>本地缓存（Guava Cache）</li>
     *   <li>ZooKeeper</li>
     * </ol>
     *
     * @param customerId 客户ID
     * @return 路由节点ID，如果未找到返回Optional.empty()
     */
    public Optional<String> getRouterId(String customerId) {
        if (customerId == null || customerId.isEmpty()) {
            return Optional.empty();
        }

        // 1. 先查本地缓存
        String routerId = bindingCache.getIfPresent(customerId);
        if (routerId != null) {
            LOGGER.debug("从缓存获取客户绑定: customerId={}, routerId={}", customerId, routerId);
            return Optional.of(routerId);
        }

        // 2. 查询 ZK
        routerId = queryFromZk(customerId);
        if (routerId != null) {
            // 更新缓存
            bindingCache.put(customerId, routerId);
            return Optional.of(routerId);
        }

        return Optional.empty();
    }

    /**
     * 通过sessionId查询路由节点ID
     *
     * <p>坐席发送消息时，通常知道sessionId而不是customerId。
     * 此方法先通过sessionId查找customerId，再查询routerId。</p>
     *
     * <h4>前提条件：</h4>
     * <p>必须在收到SESSION_ASSIGN消息时调用{@link #registerSessionCustomer(String, String)}
     * 注册sessionId → customerId的映射关系。</p>
     *
     * @param sessionId 会话ID
     * @return 路由节点ID
     */
    public Optional<String> getRouterIdBySessionId(String sessionId) {
        String customerId = sessionCustomerMap.get(sessionId);
        if (customerId != null) {
            return getRouterId(customerId);
        }
        return Optional.empty();
    }

    /**
     * 注册会话的客户映射
     *
     * <p>当收到SESSION_ASSIGN消息时调用，建立sessionId → customerId的映射关系。
     * 这样后续可以通过sessionId快速找到customerId。</p>
     *
     * @param sessionId  会话ID
     * @param customerId 客户ID
     */
    public void registerSessionCustomer(String sessionId, String customerId) {
        if (sessionId != null && customerId != null) {
            sessionCustomerMap.put(sessionId, customerId);
            LOGGER.debug("注册会话客户映射: sessionId={}, customerId={}", sessionId, customerId);
        }
    }

    /**
     * 移除会话的客户映射
     *
     * <p>当会话关闭时调用</p>
     *
     * @param sessionId 会话ID
     */
    public void unregisterSessionCustomer(String sessionId) {
        if (sessionId != null) {
            sessionCustomerMap.remove(sessionId);
            LOGGER.debug("移除会话客户映射: sessionId={}", sessionId);
        }
    }

    /**
     * 更新本地缓存的绑定关系
     *
     * <p>当从其他渠道（如SESSION_ASSIGN消息）获知绑定关系时，
     * 可以直接更新缓存，减少ZK查询。</p>
     *
     * @param customerId 客户ID
     * @param routerId   路由节点ID
     */
    public void updateBinding(String customerId, String routerId) {
        bindingCache.put(customerId, routerId);
        LOGGER.debug("更新客户绑定缓存: customerId={}, routerId={}", customerId, routerId);
    }

    /**
     * 使缓存失效
     *
     * <p>发送失败时调用，清除可能过期的绑定关系。
     * 下次查询时将从ZK重新获取最新数据。</p>
     *
     * <h4>使用场景：</h4>
     * <pre>{@code
     * // 发送消息失败时
     * if (!sendMessage(routerId, message)) {
     *     // 可能是绑定关系过期，清除缓存
     *     customerBindingQuery.invalidate(customerId);
     *     // 重试发送
     *     String newRouterId = customerBindingQuery.getRouterId(customerId).orElse(null);
     *     if (newRouterId != null) {
     *         sendMessage(newRouterId, message);
     *     }
     * }
     * }</pre>
     *
     * @param customerId 客户ID
     */
    public void invalidate(String customerId) {
        bindingCache.invalidate(customerId);
        LOGGER.debug("客户绑定缓存失效: customerId={}", customerId);
    }

    /**
     * 检查客户是否在线
     *
     * @param customerId 客户ID
     * @return 是否在线（有绑定关系）
     */
    public boolean isCustomerOnline(String customerId) {
        return getRouterId(customerId).isPresent();
    }

    /**
     * 获取所有缓存的绑定关系
     *
     * <p>用于监控和调试</p>
     *
     * @return 绑定关系副本
     */
    public Map<String, String> getAllBindings() {
        return new HashMap<>(bindingCache.asMap());
    }

    /**
     * 清空本地缓存
     */
    public void clearCache() {
        bindingCache.invalidateAll();
        sessionCustomerMap.clear();
        LOGGER.info("客户绑定缓存已清空");
    }

    /**
     * 检查 ZooKeeper 是否已连接
     *
     * @return 是否已连接
     */
    public boolean isConnected() {
        return curatorClient != null && curatorClient.getZookeeperClient().isConnected();
    }

    /**
     * 从ZK查询绑定关系
     *
     * @param customerId 客户ID
     * @return 路由节点ID，未找到返回null
     */
    private String queryFromZk(String customerId) {
        if (curatorClient == null) {
            return null;
        }

        String path = CUSTOMER_BINDINGS_PATH + "/" + customerId;

        try {
            byte[] data = curatorClient.getData().forPath(path);
            return new String(data, StandardCharsets.UTF_8);
        } catch (org.apache.zookeeper.KeeperException.NoNodeException e) {
            LOGGER.debug("客户绑定关系不存在: customerId={}", customerId);
        } catch (Exception e) {
            LOGGER.warn("查询客户绑定关系失败: customerId={}", customerId, e);
        }

        return null;
    }
}
