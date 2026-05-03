package com.example.customerserver.zookeeper;

import com.example.customerserver.config.CustomerServerProperties;
import com.example.customerserver.config.ZookeeperProperties;
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
import java.util.concurrent.TimeUnit;

/**
 * 客户绑定关系注册器
 *
 * <p>负责将客户与路由节点的绑定关系注册到 ZooKeeper。
 * 这是实现"坐席→客户"消息正确路由的关键组件。</p>
 *
 * <h3>架构位置：</h3>
 * <pre>
 *                      ┌─────────────────┐
 *                      │   ZooKeeper     │
 *                      │ /customer-bindings/{customerId} → routerId
 *                      └────────┬────────┘
 *                               │
 *        ┌──────────────────────┼──────────────────────┐
 *        │                      │                      │
 *        ▼                      ▼                      ▼
 *   ┌─────────┐           ┌─────────┐           ┌─────────┐
 *   │  CF-1   │           │  CF-2   │           │  CF-3   │
 *   │注册客户 │           │注册客户 │           │注册客户 │
 *   │绑定关系 │           │绑定关系 │           │绑定关系 │
 *   └─────────┘           └─────────┘           └─────────┘
 * </pre>
 *
 * <h3>ZK节点结构：</h3>
 * <pre>
 * /star-connection/                    # 命名空间
 * ├── /customer-bindings/              # 客户绑定根目录
 * │   ├── /customer-001 → "router-1"   # 客户001连接在router-1
 * │   ├── /customer-002 → "router-2"   # 客户002连接在router-2
 * │   └── /customer-003 → "router-1"   # 客户003连接在router-1
 * └── /agent-bindings/                 # 坐席绑定（由AB注册）
 *     ├── /agent-001 → "backend-1"
 *     └── /agent-002 → "backend-2"
 * </pre>
 *
 * <h3>消息路由流程：</h3>
 * <pre>
 * 1. 客户连接CF时：
 *    - CustomerWebSocketHandler.afterConnectionEstablished()
 *    - 调用 registerBinding(customerId, routerId)
 *    - 在ZK创建临时节点 /customer-bindings/{customerId}
 *
 * 2. 坐席回复消息时：
 *    - AB端 CustomerBindingQuery 查询 customerId → routerId
 *    - 找到正确的CF节点发送消息
 *
 * 3. 客户断开时：
 *    - CustomerWebSocketHandler.afterConnectionClosed()
 *    - 调用 unregisterBinding(customerId)
 *    - ZK临时节点自动删除（双重保障）
 * </pre>
 *
 * <h3>为什么使用临时节点：</h3>
 * <ul>
 *   <li>CF节点宕机时，所有客户连接会断开，临时节点自动删除</li>
 *   <li>客户主动断开时，代码主动删除 + 临时节点双重保障</li>
 *   <li>避免"僵尸绑定"导致消息路由错误</li>
 * </ul>
 *
 * @author Customer Service Platform Team
 * @version 1.0.0
 * @see com.example.agentserver.zookeeper.CustomerBindingQuery
 * @see com.example.customerserver.websocket.CustomerWebSocketHandler
 */
@Component
public class CustomerBindingRegistry {

    private static final Logger LOGGER = LoggerFactory.getLogger(CustomerBindingRegistry.class);

    /**
     * ZK客户绑定节点路径
     */
    private static final String CUSTOMER_BINDINGS_PATH = "/customer-bindings";

    private final ZookeeperProperties zookeeperProperties;
    private final CustomerServerProperties routerProperties;

    /**
     * Curator客户端实例
     * <p>每个注册器维护自己的客户端，便于独立管理生命周期</p>
     */
    private CuratorFramework curatorClient;

    @Autowired
    public CustomerBindingRegistry(ZookeeperProperties zookeeperProperties,
                                    CustomerServerProperties routerProperties) {
        this.zookeeperProperties = zookeeperProperties;
        this.routerProperties = routerProperties;
    }

    /**
     * 初始化方法
     * <p>Spring容器启动后自动调用，创建ZK连接并确保父节点存在</p>
     */
    @PostConstruct
    public void init() {
        LOGGER.info("初始化客户绑定关系注册器...");

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
                // 确保父节点存在
                ensurePathExists(CUSTOMER_BINDINGS_PATH);
                LOGGER.info("客户绑定关系注册器初始化成功");
            } else {
                LOGGER.warn("连接 ZooKeeper 超时，客户绑定注册将不可用");
            }
        } catch (Exception e) {
            LOGGER.warn("客户绑定关系注册器初始化失败: {}. 客户绑定功能将不可用。", e.getMessage());
            LOGGER.debug("初始化错误详情", e);
        }
    }

    /**
     * 销毁方法
     * <p>Spring容器关闭前自动调用，优雅关闭ZK连接</p>
     */
    @PreDestroy
    public void destroy() {
        LOGGER.info("关闭客户绑定关系注册器...");
        if (curatorClient != null) {
            curatorClient.close();
        }
        LOGGER.info("客户绑定关系注册器已关闭");
    }

    /**
     * 注册客户绑定关系
     *
     * <p>在ZK创建临时节点，存储客户与路由节点的映射关系。
     * 当客户连接断开或CF节点宕机时，临时节点会自动删除。</p>
     *
     * <h4>调用时机：</h4>
     * <ul>
     *   <li>客户WebSocket连接建立时</li>
     *   <li>客户重连时（更新绑定关系）</li>
     * </ul>
     *
     * @param customerId 客户ID，如：customer-12345678
     * @param routerId   路由节点ID，如：router-1
     */
    public void registerBinding(String customerId, String routerId) {
        if (curatorClient == null) {
            LOGGER.warn("ZooKeeper 客户端未初始化，无法注册客户绑定");
            return;
        }

        String path = CUSTOMER_BINDINGS_PATH + "/" + customerId;

        try {
            // 使用临时节点，会话断开时自动删除
            if (curatorClient.checkExists().forPath(path) != null) {
                // 更新已存在的节点（客户重连场景）
                curatorClient.setData().forPath(path, routerId.getBytes(StandardCharsets.UTF_8));
                LOGGER.debug("更新客户绑定关系: customerId={}, routerId={}", customerId, routerId);
            } else {
                // 创建临时节点
                curatorClient.create()
                        .creatingParentsIfNeeded()
                        .withMode(org.apache.zookeeper.CreateMode.EPHEMERAL)
                        .forPath(path, routerId.getBytes(StandardCharsets.UTF_8));
                LOGGER.info("注册客户绑定关系: customerId={}, routerId={}", customerId, routerId);
            }
        } catch (Exception e) {
            LOGGER.error("注册客户绑定关系失败: customerId={}, routerId={}", customerId, routerId, e);
        }
    }

    /**
     * 注销客户绑定关系
     *
     * <p>主动删除ZK节点。通常在客户主动断开连接时调用。
     * 即使不调用此方法，临时节点也会在会话断开时自动删除。</p>
     *
     * <h4>调用时机：</h4>
     * <ul>
     *   <li>客户WebSocket连接关闭时</li>
     *   <li>会话结束时</li>
     * </ul>
     *
     * @param customerId 客户ID
     */
    public void unregisterBinding(String customerId) {
        if (curatorClient == null) {
            LOGGER.warn("ZooKeeper 客户端未初始化，无法注销客户绑定");
            return;
        }

        String path = CUSTOMER_BINDINGS_PATH + "/" + customerId;

        try {
            if (curatorClient.checkExists().forPath(path) != null) {
                curatorClient.delete().forPath(path);
                LOGGER.info("注销客户绑定关系: customerId={}", customerId);
            }
        } catch (Exception e) {
            LOGGER.error("注销客户绑定关系失败: customerId={}", customerId, e);
        }
    }

    /**
     * 更新客户绑定关系
     *
     * <p>客户重连到不同的CF节点时调用，更新绑定关系指向新的路由节点。</p>
     *
     * @param customerId 客户ID
     * @param routerId   新的路由节点ID
     */
    public void updateBinding(String customerId, String routerId) {
        registerBinding(customerId, routerId);
    }

    /**
     * 获取当前路由节点ID
     *
     * <p>返回当前CF节点的serviceId，用于注册绑定关系。</p>
     *
     * @return 当前路由节点ID，如：router-1
     */
    public String getCurrentRouterId() {
        return routerProperties.getServiceId();
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
     * 确保ZK节点路径存在
     *
     * <p>如果路径不存在，创建为持久节点。</p>
     *
     * @param path ZK节点路径
     * @throws Exception 如果创建失败
     */
    private void ensurePathExists(String path) throws Exception {
        if (curatorClient.checkExists().forPath(path) == null) {
            curatorClient.create()
                    .creatingParentsIfNeeded()
                    .withMode(org.apache.zookeeper.CreateMode.PERSISTENT)
                    .forPath(path);
        }
    }
}
