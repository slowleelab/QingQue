package com.example.customerserver.zookeeper;

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
import org.springframework.stereotype.Component;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;

/**
 * 服务发现组件，用于查找客户端服务
 */
@Component
public class RouterServiceDiscovery {
    private static final Logger LOGGER = LoggerFactory.getLogger(ServiceDiscovery.class);

    private final ZookeeperProperties zookeeperProperties;
    private CuratorFramework client;
    private org.apache.curator.x.discovery.ServiceDiscovery<Map<String, String>> serviceDiscovery;

    @Autowired
    public RouterServiceDiscovery(ZookeeperProperties zookeeperProperties) {
        this.zookeeperProperties = zookeeperProperties;
    }

    @PostConstruct
    public void init() {
        LOGGER.info("正在初始化 ZooKeeper 服务发现...");

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
            // 等待连接，最多等待 5 秒
            if (!client.blockUntilConnected(5, java.util.concurrent.TimeUnit.SECONDS)) {
                throw new Exception("连接 ZooKeeper 超时");
            }

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
                            .build();

            serviceDiscovery = (org.apache.curator.x.discovery.ServiceDiscovery<Map<String, String>>) discovery;

            serviceDiscovery.start();

            LOGGER.info("ZooKeeper 服务发现初始化成功");
        } catch (Exception e) {
            LOGGER.warn("ZooKeeper 服务发现初始化失败: {}. 服务将在没有 ZooKeeper 支持的情况下继续运行。", e.getMessage());
            LOGGER.debug("ZooKeeper 初始化错误详情", e);
        }
    }

    @PreDestroy
    public void destroy() {
        LOGGER.info("正在关闭 ZooKeeper 服务发现...");

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

        LOGGER.info("ZooKeeper 服务发现已关闭");
    }

    /**
     * 获取所有已注册的服务
     */
    public Collection<ServiceInstance<Map<String, String>>> getAllServices() throws Exception {
        if (serviceDiscovery == null) {
            return Collections.emptyList();
        }
        Collection<ServiceInstance<Map<String, String>>> allInstances = new ArrayList<>();
        // 先获取所有服务名称
        Collection<String> serviceNames = serviceDiscovery.queryForNames();
        for (String serviceName : serviceNames) {
            Collection<ServiceInstance<Map<String, String>>> instances = serviceDiscovery.queryForInstances(serviceName);
            allInstances.addAll(instances);
        }
        return allInstances;
    }

    /**
     * 根据名称获取服务
     */
    public Collection<ServiceInstance<Map<String, String>>> getServicesByName(String serviceName) throws Exception {
        if (serviceDiscovery == null) {
            return Collections.emptyList();
        }
        return serviceDiscovery.queryForInstances(serviceName);
    }

    /**
     * 根据ID获取服务
     */
    public ServiceInstance<Map<String, String>> getServiceById(String serviceId) throws Exception {
        if (serviceDiscovery == null) {
            return null;
        }

        Collection<ServiceInstance<Map<String, String>>> allServices = getAllServices();
        for (ServiceInstance<Map<String, String>> instance : allServices) {
            if (instance.getId().equals(serviceId)) {
                return instance;
            }
        }

        return null;
    }

    /**
     * 检查服务是否已注册
     */
    public boolean isServiceRegistered(String serviceId) throws Exception {
        return getServiceById(serviceId) != null;
    }

    /**
     * 获取服务元数据
     */
    public Map<String, String> getServiceMetadata(String serviceId) throws Exception {
        ServiceInstance<Map<String, String>> instance = getServiceById(serviceId);
        if (instance != null) {
            Map<String, String> payload = instance.getPayload();
            return payload != null ? payload : new HashMap<>();
        }
        return new HashMap<>();
    }

    /**
     * 从 ZooKeeper 获取已连接客户端数量
     */
    public int getRegisteredServiceCount() {
        try {
            return getAllServices().size();
        } catch (Exception e) {
            LOGGER.error("获取已注册服务数量失败", e);
            return 0;
        }
    }

    /**
     * 检查 ZooKeeper 是否已连接
     */
    public boolean isConnected() {
        return client != null && client.getZookeeperClient().isConnected();
    }

    /**
     * 获取连接字符串
     */
    public String getConnectString() {
        return zookeeperProperties.getConnectString();
    }
}