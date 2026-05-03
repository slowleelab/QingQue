package com.example.customerserver.message;

import com.example.common.model.ChatMessage;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * 客户消息存储
 * 用于 HTTP 长轮询方式的消息推送
 */
@Component
public class CustomerMessageStore {
    private static final Logger LOGGER = LoggerFactory.getLogger(CustomerMessageStore.class);

    /**
     * 每个会话的消息队列
     */
    private final Map<String, LinkedBlockingQueue<ChatMessage>> sessionMessages = new ConcurrentHashMap<>();

    /**
     * 长轮询超时时间（毫秒）
     */
    private static final long POLL_TIMEOUT_MS = 30000;

    /**
     * 每个会话最大消息数
     */
    private static final int MAX_MESSAGES_PER_SESSION = 100;

    /**
     * 添加消息到会话队列
     */
    public void addMessage(String sessionId, ChatMessage message) {
        LinkedBlockingQueue<ChatMessage> queue = sessionMessages.computeIfAbsent(
                sessionId, k -> new LinkedBlockingQueue<>(MAX_MESSAGES_PER_SESSION));

        // 如果队列满了，移除最旧的消息
        if (!queue.offer(message)) {
            queue.poll();
            queue.offer(message);
            LOGGER.warn("会话消息队列已满，移除最旧消息: sessionId={}", sessionId);
        }

        LOGGER.debug("消息已添加到队列: sessionId={}, messageId={}", sessionId, message.getMessageId());
    }

    /**
     * 长轮询获取消息
     * 阻塞等待直到有消息或超时
     */
    public List<ChatMessage> pollMessages(String sessionId, long timeoutMs) {
        LinkedBlockingQueue<ChatMessage> queue = sessionMessages.get(sessionId);
        if (queue == null) {
            queue = sessionMessages.computeIfAbsent(
                    sessionId, k -> new LinkedBlockingQueue<>(MAX_MESSAGES_PER_SESSION));
        }

        List<ChatMessage> messages = new ArrayList<>();

        try {
            // 阻塞等待第一条消息
            ChatMessage firstMessage = queue.poll(timeoutMs > 0 ? timeoutMs : POLL_TIMEOUT_MS, TimeUnit.MILLISECONDS);
            if (firstMessage != null) {
                messages.add(firstMessage);
                // 非阻塞获取剩余消息
                while (true) {
                    ChatMessage msg = queue.poll();
                    if (msg == null) {
                        break;
                    }
                    messages.add(msg);
                }
            }
        } catch (InterruptedException e) {
            LOGGER.debug("长轮询被中断: sessionId={}", sessionId);
            Thread.currentThread().interrupt();
        }

        return messages;
    }

    /**
     * 非阻塞获取所有待处理消息
     */
    public List<ChatMessage> getPendingMessages(String sessionId) {
        LinkedBlockingQueue<ChatMessage> queue = sessionMessages.get(sessionId);
        if (queue == null) {
            return new ArrayList<>();
        }

        List<ChatMessage> messages = new ArrayList<>();
        queue.drainTo(messages);
        return messages;
    }

    /**
     * 清除会话消息队列
     */
    public void clearSession(String sessionId) {
        sessionMessages.remove(sessionId);
        LOGGER.debug("已清除会话消息队列: sessionId={}", sessionId);
    }

    /**
     * 获取会话待处理消息数
     */
    public int getPendingCount(String sessionId) {
        LinkedBlockingQueue<ChatMessage> queue = sessionMessages.get(sessionId);
        return queue != null ? queue.size() : 0;
    }

    /**
     * 获取所有会话的待处理消息统计
     */
    public Map<String, Integer> getAllPendingCounts() {
        Map<String, Integer> counts = new ConcurrentHashMap<>();
        sessionMessages.forEach((sessionId, queue) -> counts.put(sessionId, queue.size()));
        return counts;
    }
}
