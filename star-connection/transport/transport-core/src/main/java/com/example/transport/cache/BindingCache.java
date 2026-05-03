package com.example.transport.cache;

import com.google.common.cache.Cache;
import com.google.common.cache.CacheBuilder;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.HashMap;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.TimeUnit;

/**
 * 绑定关系缓存
 *
 * <p>这是一个高性能的本地缓存组件，用于存储服务绑定关系。
 * 在客服系统中，主要用于缓存以下映射关系：</p>
 *
 * <ul>
 *   <li><b>CF端</b>：agentId → backendId（坐席连接的后台节点）</li>
 *   <li><b>AB端</b>：customerId → routerId（客户连接的前置节点）</li>
 * </ul>
 *
 * <h3>缓存策略：</h3>
 * <ul>
 *   <li><b>TTL过期</b>：写入后30秒自动过期，确保数据不会过于陈旧</li>
 *   <li><b>容量限制</b>：最大10万条，超过后采用LRU淘汰</li>
 *   <li><b>主动失效</b>：发送失败时主动调用{@link #invalidate(String)}清除缓存</li>
 * </ul>
 *
 * <h3>性能考虑：</h3>
 * <p>对于千万级消息量的系统，本地缓存可以显著减少ZK查询压力。
 * 假设平均每秒3000条消息，缓存命中率95%，则：</p>
 * <ul>
 *   <li>缓存命中：2850次/秒（本地内存读取，延迟<1ms）</li>
 *   <li>缓存未命中：150次/秒（需要查询ZK，延迟~5ms）</li>
 * </ul>
 *
 * <h3>使用示例：</h3>
 * <pre>{@code
 * // 创建缓存（通常由Spring自动注入）
 * BindingCache cache = new BindingCache(30, TimeUnit.SECONDS, 100000);
 *
 * // 存入绑定关系
 * cache.put("agent-001", "agent-backend-1");
 *
 * // 查询绑定关系
 * Optional<String> backendId = cache.get("agent-001");
 *
 * // 发送失败时失效缓存
 * if (!sendSuccess) {
 *     cache.invalidate("agent-001");
 *     // 重新查询ZK获取最新绑定
 * }
 * }</pre>
 *
 * <h3>缓存一致性：</h3>
 * <p>本缓存采用"最终一致性"模型：</p>
 * <ol>
 *   <li>正常情况：TTL到期后自动刷新</li>
 *   <li>异常情况：发送失败后主动失效，下次查询重新获取</li>
 *   <li>极端情况：最多延迟TTL时间获取到最新数据</li>
 * </ol>
 *
 * @author Customer Service Platform Team
 * @version 1.0.0
 * @see com.example.customerserver.zookeeper.AgentBindingQuery
 * @see com.example.agentserver.zookeeper.CustomerBindingQuery
 */
public class BindingCache {

    private static final Logger LOGGER = LoggerFactory.getLogger(BindingCache.class);

    /**
     * Guava缓存实例
     * <p>使用Guava Cache的原因：</p>
     * <ul>
     *   <li>线程安全</li>
     *   <li>支持TTL和容量限制</li>
     *   <li>支持统计信息</li>
     *   <li>高性能（基于ConcurrentHashMap）</li>
     * </ul>
     */
    private final Cache<String, String> cache;

    /**
     * 默认构造函数
     * <p>使用默认配置：TTL 30秒，最大10万条</p>
     */
    public BindingCache() {
        this(30, TimeUnit.SECONDS, 100000);
    }

    /**
     * 自定义构造函数
     *
     * @param ttl     过期时间
     * @param ttlUnit 时间单位
     * @param maxSize 最大缓存条数
     */
    public BindingCache(long ttl, TimeUnit ttlUnit, long maxSize) {
        this.cache = CacheBuilder.newBuilder()
                .expireAfterWrite(ttl, ttlUnit)  // 写入后TTL过期
                .maximumSize(maxSize)            // 最大容量限制
                .recordStats()                   // 记录统计信息，用于监控
                .build();
        LOGGER.info("绑定缓存初始化完成: TTL={}{}, maxSize={}", ttl, ttlUnit, maxSize);
    }

    /**
     * 获取绑定关系
     *
     * @param key 键（agentId 或 customerId）
     * @return 绑定的值（backendId 或 routerId），不存在返回Optional.empty()
     */
    public Optional<String> get(String key) {
        if (key == null) {
            return Optional.empty();
        }
        String value = cache.getIfPresent(key);
        if (value != null) {
            LOGGER.debug("缓存命中: key={}, value={}", key, value);
        }
        return Optional.ofNullable(value);
    }

    /**
     * 存入绑定关系
     *
     * @param key   键（agentId 或 customerId）
     * @param value 值（backendId 或 routerId）
     */
    public void put(String key, String value) {
        if (key != null && value != null) {
            cache.put(key, value);
            LOGGER.debug("缓存存入: key={}, value={}", key, value);
        }
    }

    /**
     * 使缓存失效
     * <p>当发送消息失败时，应调用此方法清除可能过期的绑定关系。
     * 下次查询时将从ZK重新获取最新数据。</p>
     *
     * @param key 键
     */
    public void invalidate(String key) {
        if (key != null) {
            cache.invalidate(key);
            LOGGER.debug("缓存失效: key={}", key);
        }
    }

    /**
     * 批量失效所有缓存
     * <p>用于系统重置或大规模故障恢复场景</p>
     */
    public void invalidateAll() {
        cache.invalidateAll();
        LOGGER.info("所有缓存已失效");
    }

    /**
     * 获取所有缓存内容
     * <p>用于监控和调试</p>
     *
     * @return 缓存内容的副本
     */
    public Map<String, String> getAll() {
        return new HashMap<>(cache.asMap());
    }

    /**
     * 获取当前缓存大小
     *
     * @return 缓存条数
     */
    public long size() {
        return cache.size();
    }

    /**
     * 获取缓存统计信息
     * <p>用于监控缓存效率，建议定期输出到监控系统</p>
     *
     * @return 缓存统计信息
     */
    public CacheStats getStats() {
        com.google.common.cache.CacheStats stats = cache.stats();
        return new CacheStats(
                stats.hitCount(),
                stats.missCount(),
                stats.hitRate(),
                stats.evictionCount(),
                stats.loadCount()
        );
    }

    /**
     * 缓存统计信息
     * <p>包含缓存命中等各项指标</p>
     */
    public static class CacheStats {
        /** 命中次数 */
        private final long hitCount;
        /** 未命中次数 */
        private final long missCount;
        /** 命中率 (0.0 - 1.0) */
        private final double hitRate;
        /** 淘汰次数 */
        private final long evictionCount;
        /** 加载次数 */
        private final long loadCount;

        public CacheStats(long hitCount, long missCount, double hitRate,
                          long evictionCount, long loadCount) {
            this.hitCount = hitCount;
            this.missCount = missCount;
            this.hitRate = hitRate;
            this.evictionCount = evictionCount;
            this.loadCount = loadCount;
        }

        public long getHitCount() {
            return hitCount;
        }

        public long getMissCount() {
            return missCount;
        }

        public double getHitRate() {
            return hitRate;
        }

        public long getEvictionCount() {
            return evictionCount;
        }

        public long getLoadCount() {
            return loadCount;
        }

        @Override
        public String toString() {
            return String.format("CacheStats{hits=%d, misses=%d, hitRate=%.2f%%, evictions=%d}",
                    hitCount, missCount, hitRate * 100, evictionCount);
        }
    }
}
