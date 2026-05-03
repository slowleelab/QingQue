package com.example.transport.autoconfigure;

import com.example.transport.cache.BindingCache;
import com.example.transport.connection.ConnectionPool;
import com.example.transport.heartbeat.HeartbeatConfig;
import com.example.transport.netty.NettyConnectionPool;
import com.example.transport.reconnection.ExponentialBackoffPolicy;
import com.example.transport.reconnection.ReconnectionPolicy;
import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;

import java.util.concurrent.TimeUnit;

/**
 * Transport 自动配置
 * 为 CF 和 AB 模块提供统一的传输层组件
 */
@AutoConfiguration
@EnableConfigurationProperties(TransportProperties.class)
public class TransportAutoConfiguration {

    /**
     * 连接池
     */
    @Bean
    @ConditionalOnMissingBean
    public ConnectionPool connectionPool() {
        return new NettyConnectionPool();
    }

    /**
     * 绑定关系缓存
     * 用于存储 customerId -> routerId 或 agentId -> backendId 的映射
     */
    @Bean
    @ConditionalOnMissingBean
    public BindingCache bindingCache(TransportProperties properties) {
        TransportProperties.HeartbeatProperties heartbeat = properties.getHeartbeat();
        // 缓存 TTL 设为心跳间隔的 1.5 倍，确保缓存过期前能收到心跳更新
        long ttlSeconds = (long) (heartbeat.getIntervalSeconds() * 1.5);
        return new BindingCache(ttlSeconds, TimeUnit.SECONDS, 100000);
    }

    /**
     * 心跳配置
     */
    @Bean
    @ConditionalOnMissingBean
    public HeartbeatConfig heartbeatConfig(TransportProperties properties) {
        TransportProperties.HeartbeatProperties heartbeatProps = properties.getHeartbeat();
        HeartbeatConfig config = new HeartbeatConfig();
        config.setIntervalSeconds(heartbeatProps.getIntervalSeconds());
        config.setTimeoutSeconds(heartbeatProps.getTimeoutSeconds());
        config.setMaxMissedHeartbeats(heartbeatProps.getMaxMissed());
        config.setEnabled(heartbeatProps.isEnabled());
        return config;
    }

    /**
     * 重连策略
     */
    @Bean
    @ConditionalOnMissingBean
    public ReconnectionPolicy reconnectionPolicy(TransportProperties properties) {
        TransportProperties.ReconnectionProperties reconnProps = properties.getReconnection();
        return ExponentialBackoffPolicy.builder()
                .initialDelayMs(reconnProps.getInitialDelayMs())
                .maxDelayMs(reconnProps.getMaxDelayMs())
                .maxRetries(reconnProps.getMaxRetries())
                .multiplier(2.0)
                .jitterFactor(reconnProps.getJitterFactor())
                .build();
    }
}
