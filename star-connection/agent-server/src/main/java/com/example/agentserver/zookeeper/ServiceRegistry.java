package com.example.agentserver.zookeeper;

import com.example.agentserver.config.AgentServerProperties;
import com.example.agentserver.config.ZookeeperProperties;
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
import org.springframework.stereotype.Component;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import java.net.InetAddress;
import java.util.HashMap;
import java.util.Map;

/**
 * 服务注册组件，用于向 ZooKeeper 注册客户端
 */
@Component
public class ServiceRegistry {
    private static final Logger LOGGER = LoggerFactory.getLogger(ServiceRegistry.class);

    private final ZookeeperProperties zookeeperProperties;
    private final AgentServerProperties clientProperties;

    private CuratorFramework client;
    private ServiceDiscovery<Map<String, String>> serviceDiscovery;
    private ServiceInstance<Map<String, String>> serviceInstance;

    @Autowired
    public ServiceRegistry(ZookeeperProperties zookeeperProperties, AgentServerProperties clientProperties) {
        this.zookeeperProperties = zookeeperProperties;
        this.clientProperties = clientProperties;
    }

    @PostConstruct
    public void init() throws Exception {
        if (clientProperties.getServiceId() == null) {
            LOGGER.warn("服务ID未设置，跳过 ZooKeeper 注册");
            return;
        }

        LOGGER.info("正在向 ZooKeeper 注册服务 {}...", clientProperties.getServiceId());

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
        // 等待连接，最多等待 5 秒
        if (!client.blockUntilConnected(5, java.util.concurrent.TimeUnit.SECONDS)) {
            LOGGER.warn("连接 ZooKeeper 超时，服务将跳过注册");
            return;
        }

        // 获取本地IP地址
        String ipAddress = InetAddress.getLocalHost().getHostAddress();

        // 创建服务实例元数据
        Map<String, String> metadata = new HashMap<>();
        metadata.put("service-name", clientProperties.getServiceName());
        metadata.put("ip-address", ipAddress);
        metadata.put("startup-time", String.valueOf(System.currentTimeMillis()));

        // 创建服务实例
        serviceInstance = ServiceInstance.<Map<String, String>>builder()
                .name(clientProperties.getServiceName())
                .id(clientProperties.getServiceId())
                .address(ipAddress)
                .port(0) // Netty连接不需要
                .payload(metadata)
                .build();

        // 创建服务发现
        // 解决 Curator 泛型类型问题
        @SuppressWarnings({"rawtypes", "unchecked"})
        JsonInstanceSerializer serializer = new JsonInstanceSerializer(Map.class);

        @SuppressWarnings("rawtypes")
        org.apache.curator.x.discovery.ServiceDiscovery discovery =
                ServiceDiscoveryBuilder.builder(Map.class)
                        .client(client)
                        .basePath(zookeeperProperties.getServicePath())
                        .serializer(serializer)
                        .thisInstance(serviceInstance)
                        .build();

        serviceDiscovery = (org.apache.curator.x.discovery.ServiceDiscovery<Map<String, String>>) discovery;

        serviceDiscovery.start();

        LOGGER.info("服务 {} 成功注册到 ZooKeeper", clientProperties.getServiceId());
    }

    @PreDestroy
    public void destroy() {
        LOGGER.info("正在从 ZooKeeper 注销服务 {}...", clientProperties.getServiceId());

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

        LOGGER.info("服务已从 ZooKeeper 注销");
    }

    /**
     * 更新服务元数据
     */
    public void updateMetadata(Map<String, String> newMetadata) throws Exception {
        if (serviceInstance == null || serviceDiscovery == null) {
            LOGGER.warn("服务未注册，无法更新元数据");
            return;
        }

        Map<String, String> currentMetadata = serviceInstance.getPayload();
        if (currentMetadata == null) {
            currentMetadata = new HashMap<>();
        }

        currentMetadata.putAll(newMetadata);

        // 创建更新的服务实例
        ServiceInstance<Map<String, String>> updatedInstance = ServiceInstance.<Map<String, String>>builder()
                .name(serviceInstance.getName())
                .id(serviceInstance.getId())
                .address(serviceInstance.getAddress())
                .port(serviceInstance.getPort())
                .payload(currentMetadata)
                .build();

        // 使用更新的元数据重新注册
        serviceDiscovery.updateService(updatedInstance);
        serviceInstance = updatedInstance;

        LOGGER.debug("服务元数据已更新");
    }

    /**
     * 获取服务实例
     */
    public ServiceInstance<Map<String, String>> getServiceInstance() {
        return serviceInstance;
    }

    /**
     * 检查服务是否已注册
     */
    public boolean isRegistered() {
        return serviceInstance != null;
    }
}