package com.example.agentserver.netty.manager;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.concurrent.ThreadPoolTaskScheduler;
import org.springframework.stereotype.Component;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import java.util.Date;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 重连管理器，支持指数退避
 */
@Component
public class ReconnectionManager {
    private static final Logger LOGGER = LoggerFactory.getLogger(ReconnectionManager.class);

    private static final int MAX_RETRY_COUNT = 10;
    private static final int INITIAL_DELAY_SECONDS = 1;
    private static final int MAX_DELAY_SECONDS = 300; // 5分钟

    private final ThreadPoolTaskScheduler taskScheduler;
    private final AtomicInteger retryCount = new AtomicInteger(0);
    private ScheduledFuture<?> reconnectionFuture;

    public ReconnectionManager() {
        this.taskScheduler = new ThreadPoolTaskScheduler();
        this.taskScheduler.setPoolSize(1);
        this.taskScheduler.setThreadNamePrefix("reconnection-");
        this.taskScheduler.initialize();
    }

    @PostConstruct
    public void init() {
        LOGGER.debug("重连管理器已初始化");
    }

    @PreDestroy
    public void destroy() {
        cancel();
        taskScheduler.shutdown();
    }

    /**
     * 使用指数退避安排重连
     */
    public synchronized void scheduleReconnection(Runnable reconnectionTask) {
        if (reconnectionFuture != null && !reconnectionFuture.isDone()) {
            LOGGER.debug("重连已安排");
            return;
        }

        int currentRetry = retryCount.incrementAndGet();
        if (currentRetry > MAX_RETRY_COUNT) {
            LOGGER.error("超过最大重连次数 ({})，放弃重连", MAX_RETRY_COUNT);
            return;
        }

        // 使用指数退避和抖动计算延迟
        long delay = calculateBackoffDelay(currentRetry);
        LOGGER.info("安排第 {} 次重连尝试，延迟 {} 秒", currentRetry, delay);

        reconnectionFuture = taskScheduler.schedule(() -> {
            try {
                reconnectionTask.run();
            } catch (Exception e) {
                LOGGER.error("重连尝试失败", e);
                // 如果此次失败，安排下一次尝试
                scheduleReconnection(reconnectionTask);
            }
        }, new Date(System.currentTimeMillis() + TimeUnit.SECONDS.toMillis(delay)));
    }

    /**
     * 使用指数退避和抖动计算退避延迟
     */
    private long calculateBackoffDelay(int retry) {
        // 指数退避: delay = base * 2^(retry-1)
        long delay = (long) (INITIAL_DELAY_SECONDS * Math.pow(2, retry - 1));

        // 添加抖动 (±25%)
        double jitter = 0.25;
        double minMultiplier = 1 - jitter;
        double maxMultiplier = 1 + jitter;
        double multiplier = minMultiplier + Math.random() * (maxMultiplier - minMultiplier);
        delay = (long) (delay * multiplier);

        // 上限为最大延迟
        return Math.min(delay, MAX_DELAY_SECONDS);
    }

    /**
     * 重置重试计数（成功连接后调用）
     */
    public synchronized void reset() {
        retryCount.set(0);
        cancel();
        LOGGER.debug("重连管理器已重置");
    }

    /**
     * 取消已安排的重连
     */
    public synchronized void cancel() {
        if (reconnectionFuture != null) {
            reconnectionFuture.cancel(false);
            reconnectionFuture = null;
            LOGGER.debug("重连已取消");
        }
    }

    /**
     * 获取当前重试计数
     */
    public int getRetryCount() {
        return retryCount.get();
    }

    /**
     * 检查是否已安排重连
     */
    public boolean isReconnectionScheduled() {
        return reconnectionFuture != null && !reconnectionFuture.isDone();
    }
}