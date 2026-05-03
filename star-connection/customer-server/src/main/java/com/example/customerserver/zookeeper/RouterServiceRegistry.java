package com.example.customerserver.zookeeper;

import com.example.customerserver.config.CustomerServerProperties;
import com.example.customerserver.config.NettyProperties;
import com.example.customerserver.config.ZookeeperProperties;
import org.apache.curator.framework.CuratorFramework;
import org.apache.curator.framework.CuratorFrameworkFactory;
import org.apache.curator.retry.ExponentialBackoffRetry;
import org.apache.curator.x.discovery.ServiceDiscovery;
import org.apache.curator.x.discovery.ServiceDiscoveryBuilder;
import org.apache.curator.x.discovery.ServiceInstance;
import org.apache.curator.x.discovery.details.JsonInstanceSerializer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import java.net.InetAddress;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.TimeUnit;

/**
 * 路由节点服务注册组件
 * 将路由节点注册到 ZooKeeper，让客户端可以发现
 */
@Component
public class RouterServiceRegistry {
    private static final Logger LOGGER = LoggerFactory.getLogger(RouterServiceRegistry.class);

    private final ZookeeperProperties zookeeperProperties;
    private final CustomerServerProperties routerProperties;
    private final NettyProperties nettyProperties;

    @Value("${server.port:8080}")
    private int serverPort;

    private CuratorFramework client;
    private ServiceDiscovery<Map<String, String>> serviceDiscovery;
    private ServiceInstance<Map<String, String>> serviceInstance;

    @Autowired
    public RouterServiceRegistry(ZookeeperProperties zookeeperProperties,
                                  CustomerServerProperties routerProperties,
                                  NettyProperties nettyProperties) {
        this.zookeeperProperties = zookeeperProperties;
        this.routerProperties = routerProperties;
        this.nettyProperties = nettyProperties;
    }

    @PostConstruct
    public void init() {
        if (!routerProperties.isRegisterToZookeeper()) {
            LOGGER.info("路由节点服务注册已禁用");
            return;
        }

        LOGGER.info("正在向 ZooKeeper 注册路由节点 {}...", routerProperties.getServiceId());

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
                LOGGER.warn("连接 ZooKeeper 超时，路由节点将在没有注册的情况下继续运行");
                return;
            }

            // 获取本地IP地址
            String ipAddress = InetAddress.getLocalHost().getHostAddress();

            // 创建服务实例元数据
            Map<String, String> metadata = new HashMap<>();
            metadata.put("service-type", "router");
            metadata.put("netty-port", String.valueOf(nettyProperties.getPort()));
            metadata.put("http-port", String.valueOf(serverPort));
            metadata.put("startup-time", String.valueOf(System.currentTimeMillis()));

            // 创建服务实例
            serviceInstance = ServiceInstance.<Map<String, String>>builder()
                    .name(routerProperties.getServiceName())
                    .id(routerProperties.getServiceId())
                    .address(ipAddress)
                    .port(nettyProperties.getPort())
                    .payload(metadata)
                    .build();

            // 创建服务发现
            @SuppressWarnings({"rawtypes", "unchecked"})
            JsonInstanceSerializer serializer = new JsonInstanceSerializer(Map.class);

            @SuppressWarnings("rawtypes")
            ServiceDiscovery discovery = ServiceDiscoveryBuilder.builder(Map.class)
                    .client(client)
                    .basePath(zookeeperProperties.getServicePath())
                    .serializer(serializer)
                    .thisInstance(serviceInstance)
                    .build();

            serviceDiscovery = (ServiceDiscovery<Map<String, String>>) discovery;
            serviceDiscovery.start();

            LOGGER.info("路由节点 {} 已成功注册到 ZooKeeper (地址: {}:{})",
                    routerProperties.getServiceId(), ipAddress, nettyProperties.getPort());
        } catch (Exception e) {
            LOGGER.warn("路由节点注册到 ZooKeeper 失败: {}. 服务将在没有注册的情况下继续运行。", e.getMessage());
            LOGGER.debug("ZooKeeper 注册错误详情", e);
        }
    }

    @PreDestroy
    public void destroy() {
        if (!routerProperties.isRegisterToZookeeper()) {
            return;
        }

        LOGGER.info("正在从 ZooKeeper 注销路由节点 {}...", routerProperties.getServiceId());

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

        LOGGER.info("路由节点已从 ZooKeeper 注销");
    }

    /**
     * 获取服务实例
     */
    public ServiceInstance<Map<String, String>> getServiceInstance() {
        return serviceInstance;
    }

    /**
     * 检查是否已注册
     */
    public boolean isRegistered() {
        return serviceInstance != null;
    }

    /**
     * 检查 ZooKeeper 是否已连接
     */
    public boolean isConnected() {
        return client != null && client.getZookeeperClient().isConnected();
    }
}
