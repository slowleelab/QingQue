package com.example.transport.reconnection;

import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 指数退避重连策略
 *
 * <p>实现了 {@link ReconnectionPolicy} 接口，使用指数退避算法计算重连延迟时间。
 * 这是分布式系统中常用的重试策略，可以避免重试风暴，给服务恢复留出时间。</p>
 *
 * <h3>算法原理：</h3>
 * <pre>
 * 延迟时间 = min(initialDelay * multiplier^(attempt-1), maxDelay) ± jitter
 * </pre>
 *
 * <h3>参数说明：</h3>
 * <ul>
 *   <li><b>initialDelayMs</b> - 初始延迟，默认1秒</li>
 *   <li><b>maxDelayMs</b> - 最大延迟，默认5分钟</li>
 *   <li><b>maxRetries</b> - 最大重试次数，默认10次</li>
 *   <li><b>multiplier</b> - 退避乘数，默认2.0</li>
 *   <li><b>jitterFactor</b> - 抖动因子，默认0.25</li>
 * </ul>
 *
 * <h3>重试延迟示例（默认配置）：</h3>
 * <pre>
 * 第1次：1秒    (1000ms ± 25% = 750~1250ms)
 * 第2次：2秒    (2000ms ± 25% = 1500~2500ms)
 * 第3次：4秒    (4000ms ± 25% = 3000~5000ms)
 * 第4次：8秒    (8000ms ± 25% = 6000~10000ms)
 * 第5次：16秒   (16000ms ± 25% = 12000~20000ms)
 * ...
 * 第10次：5分钟 (300000ms，已达上限)
 * </pre>
 *
 * <h3>为什么需要抖动（Jitter）：</h3>
 * <p>在分布式系统中，如果多个客户端同时断开并重连，没有抖动会导致"惊群效应"，
 * 所有客户端同时发起重连请求，可能导致服务端再次过载。抖动可以将重连请求
 * 分散开，避免这种情况。</p>
 *
 * <h3>使用示例：</h3>
 * <pre>{@code
 * // 使用Builder创建策略
 * ExponentialBackoffPolicy policy = ExponentialBackoffPolicy.builder()
 *     .initialDelayMs(1000)
 *     .maxDelayMs(60000)
 *     .maxRetries(5)
 *     .multiplier(2.0)
 *     .jitterFactor(0.25)
 *     .build();
 *
 * // 重连循环
 * int attempt = 1;
 * while (policy.shouldRetry(attempt)) {
 *     long delay = policy.computeDelay(attempt);
 *     Thread.sleep(delay);
 *     if (tryConnect()) {
 *         policy.reset();  // 成功后重置
 *         break;
 *     }
 *     attempt++;
 * }
 * }</pre>
 *
 * @author Customer Service Platform Team
 * @version 1.0.0
 * @see ReconnectionPolicy
 */
public class ExponentialBackoffPolicy implements ReconnectionPolicy {

    /**
     * 初始延迟（毫秒）
     * <p>第一次重试的延迟时间</p>
     */
    private final long initialDelayMs;

    /**
     * 最大延迟（毫秒）
     * <p>延迟时间的上限，防止指数增长过大</p>
     */
    private final long maxDelayMs;

    /**
     * 最大重试次数
     * <p>超过此次数后不再重试</p>
     */
    private final int maxRetries;

    /**
     * 退避乘数
     * <p>每次重试延迟的倍数，通常设为2.0</p>
     */
    private final double multiplier;

    /**
     * 抖动因子 (0.0 - 1.0)
     * <p>实际延迟 = 计算延迟 ± (计算延迟 * 抖动因子)</p>
     */
    private final double jitterFactor;

    /**
     * 当前尝试次数
     * <p>使用AtomicInteger保证线程安全</p>
     */
    private final AtomicInteger currentAttempt = new AtomicInteger(0);

    /**
     * 默认构造函数
     * <p>使用默认配置：初始1秒，最大5分钟，最多10次，乘数2.0，抖动25%</p>
     */
    public ExponentialBackoffPolicy() {
        this(1000, 300000, 10, 2.0, 0.25);
    }

    /**
     * 自定义构造函数
     *
     * @param initialDelayMs 初始延迟（毫秒），必须大于0
     * @param maxDelayMs     最大延迟（毫秒），必须大于等于initialDelayMs
     * @param maxRetries     最大重试次数，必须大于0
     * @param multiplier     退避乘数，建议1.5-2.0
     * @param jitterFactor   抖动因子 (0.0 - 1.0)，超出范围会被限制在边界值
     */
    public ExponentialBackoffPolicy(long initialDelayMs, long maxDelayMs,
                                     int maxRetries, double multiplier, double jitterFactor) {
        this.initialDelayMs = initialDelayMs;
        this.maxDelayMs = maxDelayMs;
        this.maxRetries = maxRetries;
        this.multiplier = multiplier;
        // 限制抖动因子在有效范围内
        this.jitterFactor = Math.max(0.0, Math.min(1.0, jitterFactor));
    }

    /**
     * 计算指定尝试次数的延迟时间
     *
     * <p>计算公式：</p>
     * <ol>
     *   <li>基础延迟 = initialDelayMs * multiplier^(attempt-1)</li>
     *   <li>限制最大值 = min(基础延迟, maxDelayMs)</li>
     *   <li>添加抖动 = 限制最大值 ± (限制最大值 * jitterFactor)</li>
     * </ol>
     *
     * @param attemptCount 当前尝试次数（从1开始）
     * @return 延迟时间（毫秒）
     */
    @Override
    public long computeDelay(int attemptCount) {
        // 计算指数退避延迟
        long delay = (long) (initialDelayMs * Math.pow(multiplier, attemptCount - 1));

        // 限制最大延迟
        delay = Math.min(delay, maxDelayMs);

        // 添加抖动
        if (jitterFactor > 0) {
            double jitter = delay * jitterFactor;
            double min = delay - jitter;
            double max = delay + jitter;
            delay = (long) (min + ThreadLocalRandom.current().nextDouble() * (max - min));
        }

        return Math.max(0, delay);
    }

    /**
     * 判断是否应该继续重试
     *
     * @param attemptCount 当前尝试次数
     * @return 如果未超过最大重试次数返回true
     */
    @Override
    public boolean shouldRetry(int attemptCount) {
        return attemptCount <= maxRetries;
    }

    /**
     * 重置策略状态
     * <p>连接成功后调用，重置尝试计数器</p>
     */
    @Override
    public void reset() {
        currentAttempt.set(0);
    }

    /**
     * 获取最大重试次数
     *
     * @return 最大重试次数
     */
    @Override
    public int getMaxRetries() {
        return maxRetries;
    }

    /**
     * 获取当前尝试次数
     *
     * @return 当前尝试次数
     */
    @Override
    public int getCurrentAttempt() {
        return currentAttempt.get();
    }

    /**
     * 记录一次重试
     * <p>每次重试前调用，返回当前尝试次数</p>
     *
     * @return 当前尝试次数（递增后）
     */
    public int incrementAttempt() {
        return currentAttempt.incrementAndGet();
    }

    /**
     * 创建Builder
     * <p>推荐使用Builder模式创建实例</p>
     *
     * @return Builder实例
     */
    public static Builder builder() {
        return new Builder();
    }

    /**
     * Builder类
     * <p>提供流式API构建ExponentialBackoffPolicy实例</p>
     */
    public static class Builder {
        private long initialDelayMs = 1000;
        private long maxDelayMs = 300000;
        private int maxRetries = 10;
        private double multiplier = 2.0;
        private double jitterFactor = 0.25;

        /**
         * 设置初始延迟
         *
         * @param initialDelayMs 初始延迟（毫秒）
         * @return this
         */
        public Builder initialDelayMs(long initialDelayMs) {
            this.initialDelayMs = initialDelayMs;
            return this;
        }

        /**
         * 设置最大延迟
         *
         * @param maxDelayMs 最大延迟（毫秒）
         * @return this
         */
        public Builder maxDelayMs(long maxDelayMs) {
            this.maxDelayMs = maxDelayMs;
            return this;
        }

        /**
         * 设置最大重试次数
         *
         * @param maxRetries 最大重试次数
         * @return this
         */
        public Builder maxRetries(int maxRetries) {
            this.maxRetries = maxRetries;
            return this;
        }

        /**
         * 设置退避乘数
         *
         * @param multiplier 乘数
         * @return this
         */
        public Builder multiplier(double multiplier) {
            this.multiplier = multiplier;
            return this;
        }

        /**
         * 设置抖动因子
         *
         * @param jitterFactor 抖动因子 (0.0 - 1.0)
         * @return this
         */
        public Builder jitterFactor(double jitterFactor) {
            this.jitterFactor = jitterFactor;
            return this;
        }

        /**
         * 构建ExponentialBackoffPolicy实例
         *
         * @return 新的ExponentialBackoffPolicy实例
         */
        public ExponentialBackoffPolicy build() {
            return new ExponentialBackoffPolicy(initialDelayMs, maxDelayMs,
                    maxRetries, multiplier, jitterFactor);
        }
    }
}
