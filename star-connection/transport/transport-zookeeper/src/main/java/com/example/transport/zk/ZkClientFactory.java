package com.example.transport.zk;

import org.apache.curator.framework.CuratorFramework;
import org.apache.curator.framework.CuratorFrameworkFactory;
import org.apache.curator.retry.ExponentialBackoffRetry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.concurrent.TimeUnit;

/**
 * ZooKeeper 客户端工厂
 * 提供共享的 CuratorFramework 实例
 */
public class ZkClientFactory {

    private static final Logger LOGGER = LoggerFactory.getLogger(ZkClientFactory.class);

    private final ZkConfig config;
    private volatile CuratorFramework client;
    private final Object lock = new Object();

    public ZkClientFactory(ZkConfig config) {
        this.config = config;
    }

    /**
     * 获取或创建 ZooKeeper 客户端
     */
    public CuratorFramework getClient() {
        if (client == null) {
            synchronized (lock) {
                if (client == null) {
                    client = createClient();
                }
            }
        }
        return client;
    }

    /**
     * 创建 ZooKeeper 客户端
     */
    private CuratorFramework createClient() {
        LOGGER.info("创建 ZooKeeper 客户端: connectString={}, namespace={}",
                config.getConnectString(), config.getNamespace());

        CuratorFramework curatorClient = CuratorFrameworkFactory.builder()
                .connectString(config.getConnectString())
                .sessionTimeoutMs(config.getSessionTimeoutMs())
                .connectionTimeoutMs(config.getConnectionTimeoutMs())
                .retryPolicy(new ExponentialBackoffRetry(
                        config.getBaseSleepTimeMs(),
                        config.getMaxRetries()))
                .namespace(config.getNamespace())
                .build();

        curatorClient.start();

        try {
            if (!curatorClient.blockUntilConnected(config.getConnectionTimeoutMs(), TimeUnit.MILLISECONDS)) {
                LOGGER.warn("连接 ZooKeeper 超时: connectString={}", config.getConnectString());
            } else {
                LOGGER.info("ZooKeeper 客户端连接成功");
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            LOGGER.error("等待 ZooKeeper 连接被中断", e);
        }

        return curatorClient;
    }

    /**
     * 检查是否已连接
     */
    public boolean isConnected() {
        return client != null && client.getZookeeperClient().isConnected();
    }

    /**
     * 关闭客户端
     */
    public void close() {
        if (client != null) {
            LOGGER.info("关闭 ZooKeeper 客户端");
            client.close();
            client = null;
        }
    }
}
