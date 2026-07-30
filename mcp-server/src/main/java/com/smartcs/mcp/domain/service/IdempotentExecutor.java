package com.smartcs.mcp.domain.service;

import com.smartcs.mcp.domain.port.IdempotencyStore;
import java.util.function.Supplier;
import org.springframework.stereotype.Component;

/**
 * 幂等执行器：为写类领域操作提供「同幂等键仅受理一次」的统一入口。
 *
 * <p>未提供幂等键（{@code null}/空白）时保持历史语义——直接执行、不去重；
 * 提供幂等键时委派 {@link IdempotencyStore} 保证同键重试回放原结果。</p>
 */
@Component
public class IdempotentExecutor {

    private final IdempotencyStore store;

    public IdempotentExecutor(IdempotencyStore store) {
        this.store = store;
    }

    /**
     * @param namespace      幂等命名空间（通常为工具名）
     * @param idempotencyKey 幂等键；为空表示不启用幂等
     * @param action         业务动作
     */
    public <R> R execute(String namespace, String idempotencyKey, Supplier<R> action) {
        if (idempotencyKey == null || idempotencyKey.isBlank()) {
            return action.get();
        }
        return store.computeIfAbsent(namespace, idempotencyKey.trim(), action);
    }
}
