package com.smartcs.mcp.domain.port;

import java.util.function.Supplier;

/**
 * 幂等存储端口:为写类操作提供「同幂等键仅受理一次、重试回放原结果」的保证。
 *
 * <p>当前由内存适配器实现（带 TTL 过期）；生产可替换为 Redis 等分布式实现，
 * 而不影响领域服务。</p>
 */
public interface IdempotencyStore {

    /**
     * 原子地获取或计算幂等结果:命中未过期缓存则回放,否则执行 {@code supplier} 并缓存其结果。
     * 对同一 {@code (namespace, key)} 并发调用时,{@code supplier} 至多执行一次。
     *
     * @param namespace 命名空间（通常为工具名，隔离不同工具的键空间）
     * @param key       幂等键（由调用方提供；为空表示不启用幂等，由上层直接执行）
     * @param supplier  首次执行的业务动作
     * @param <R>       结果类型
     * @return 缓存命中的原结果或本次执行的新结果
     */
    <R> R computeIfAbsent(String namespace, String key, Supplier<R> supplier);
}
