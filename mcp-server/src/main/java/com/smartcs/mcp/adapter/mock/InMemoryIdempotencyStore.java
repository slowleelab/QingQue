package com.smartcs.mcp.adapter.mock;

import com.smartcs.mcp.config.CreditCardProperties;
import com.smartcs.mcp.domain.port.IdempotencyStore;
import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Supplier;
import org.springframework.stereotype.Component;

/**
 * 内存实现的幂等存储适配器（带 TTL 过期）。
 *
 * <p>以 {@code namespace::key} 为复合键缓存首次执行结果；命中未过期缓存则回放，
 * 过期或未命中则执行 {@code supplier} 并缓存。借助 {@link ConcurrentHashMap#compute}
 * 保证同键并发下 {@code supplier} 至多执行一次。</p>
 *
 * <p>生产环境可替换为 Redis 等分布式实现（同样实现 {@link IdempotencyStore}），
 * 领域服务无需改动。</p>
 */
@Component
public class InMemoryIdempotencyStore implements IdempotencyStore {

    private record Entry(Object value, Instant expiresAt) {
        boolean isExpired(Instant now) {
            return now.isAfter(expiresAt);
        }
    }

    private final Map<String, Entry> store = new ConcurrentHashMap<>();
    private final Duration ttl;

    public InMemoryIdempotencyStore(CreditCardProperties properties) {
        this.ttl = properties.getIdempotencyTtl();
    }

    @Override
    @SuppressWarnings("unchecked")
    public <R> R computeIfAbsent(String namespace, String key, Supplier<R> supplier) {
        String compositeKey = namespace + "::" + key;
        Instant now = Instant.now();
        Entry entry = store.compute(compositeKey, (k, existing) -> {
            if (existing != null && !existing.isExpired(now)) {
                return existing;
            }
            return new Entry(supplier.get(), now.plus(ttl));
        });
        return (R) entry.value();
    }
}
