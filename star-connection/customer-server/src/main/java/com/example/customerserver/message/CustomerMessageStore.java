package com.example.customerserver.message;

import com.example.common.model.ChatMessage;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.locks.Condition;
import java.util.concurrent.locks.ReentrantLock;

/**
 * 客户消息存储
 * 基于 List + 游标（since）的非消费性读取，支持多端独立轮询。
 * 客户和坐席各自维护 lastTimestamp，互不争抢消息。
 */
@Component
public class CustomerMessageStore {
    private static final Logger LOGGER = LoggerFactory.getLogger(CustomerMessageStore.class);

    /**
     * 每个会话的消息列表（追加、不删除）
     */
    private final Map<String, List<ChatMessage>> sessionMessages = new ConcurrentHashMap<>();

    /**
     * 每个会话的锁 + 条件变量，用于长轮询阻塞/唤醒
     */
    private final Map<String, SessionLock> sessionLocks = new ConcurrentHashMap<>();

    private static final int MAX_MESSAGES_PER_SESSION = 200;
    private static final long DEFAULT_POLL_TIMEOUT_MS = 30000;

    private static class SessionLock {
        final ReentrantLock lock = new ReentrantLock();
        final Condition newMessage = lock.newCondition();
    }

    private SessionLock getLock(String sessionId) {
        return sessionLocks.computeIfAbsent(sessionId, k -> new SessionLock());
    }

    /**
     * 添加消息到会话
     */
    public void addMessage(String sessionId, ChatMessage message) {
        List<ChatMessage> messages = sessionMessages.computeIfAbsent(
                sessionId, k -> new ArrayList<>());

        synchronized (messages) {
            // 超过上限时移除最旧的一半
            if (messages.size() >= MAX_MESSAGES_PER_SESSION) {
                messages.subList(0, messages.size() / 2).clear();
                LOGGER.warn("会话消息数超限，清理旧消息: sessionId={}", sessionId);
            }
            messages.add(message);
        }

        // 唤醒所有等待的长轮询
        SessionLock sl = sessionLocks.get(sessionId);
        if (sl != null) {
            sl.lock.lock();
            try {
                sl.newMessage.signalAll();
            } finally {
                sl.lock.unlock();
            }
        }

        LOGGER.debug("消息已存储: sessionId={}, messageId={}", sessionId, message.getMessageId());
    }

    /**
     * 长轮询获取消息（非消费性，基于游标 since）
     *
     * @param sessionId 会话 ID
     * @param since     游标：只返回 timestamp > since 的消息（毫秒）
     * @param timeoutMs 长轮询超时（毫秒），0 表示不阻塞
     * @return since 之后的新消息列表，超时返回空列表
     */
    public List<ChatMessage> pollMessages(String sessionId, long since, long timeoutMs) {
        sessionMessages.computeIfAbsent(sessionId, k -> new ArrayList<>());

        long deadline = System.currentTimeMillis() + (timeoutMs > 0 ? timeoutMs : DEFAULT_POLL_TIMEOUT_MS);
        SessionLock sl = getLock(sessionId);

        sl.lock.lock();
        try {
            while (true) {
                List<ChatMessage> newMessages = getMessagesSince(sessionId, since);
                if (!newMessages.isEmpty()) {
                    return newMessages;
                }

                long remaining = deadline - System.currentTimeMillis();
                if (remaining <= 0) {
                    return new ArrayList<>();
                }

                try {
                    sl.newMessage.awaitNanos(remaining * 1_000_000L);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    return new ArrayList<>();
                }
            }
        } finally {
            sl.lock.unlock();
        }
    }

    /**
     * 获取 since 之后的消息（非阻塞，非消费）
     */
    public List<ChatMessage> getMessagesSince(String sessionId, long since) {
        List<ChatMessage> messages = sessionMessages.get(sessionId);
        if (messages == null) {
            return new ArrayList<>();
        }
        synchronized (messages) {
            List<ChatMessage> result = new ArrayList<>();
            for (ChatMessage msg : messages) {
                if (msg.getTimestamp() > since) {
                    result.add(msg);
                }
            }
            return result;
        }
    }

    /**
     * @deprecated 使用 pollMessages(sessionId, since, timeoutMs) 替代。
     *             保留此方法以兼容旧调用方（不带 since 参数，默认 since=0 获取全部）。
     */
    @Deprecated
    public List<ChatMessage> pollMessages(String sessionId, long timeoutMs) {
        return pollMessages(sessionId, 0, timeoutMs);
    }

    /**
     * 非阻塞获取所有消息（不删除，多端共享读取）
     */
    public List<ChatMessage> getPendingMessages(String sessionId) {
        List<ChatMessage> messages = sessionMessages.get(sessionId);
        if (messages == null) {
            return new ArrayList<>();
        }
        synchronized (messages) {
            return new ArrayList<>(messages);
        }
    }

    /**
     * 取出并清空消息（仅用于会话结束时归档）
     */
    public List<ChatMessage> drainMessages(String sessionId) {
        List<ChatMessage> messages = sessionMessages.get(sessionId);
        if (messages == null) {
            return new ArrayList<>();
        }
        synchronized (messages) {
            List<ChatMessage> result = new ArrayList<>(messages);
            messages.clear();
            return result;
        }
    }

    /**
     * 清除会话消息
     */
    public void clearSession(String sessionId) {
        sessionMessages.remove(sessionId);
        sessionLocks.remove(sessionId);
        LOGGER.debug("已清除会话消息: sessionId={}", sessionId);
    }

    public int getPendingCount(String sessionId) {
        List<ChatMessage> messages = sessionMessages.get(sessionId);
        return messages != null ? messages.size() : 0;
    }

    public Map<String, Integer> getAllPendingCounts() {
        Map<String, Integer> counts = new ConcurrentHashMap<>();
        sessionMessages.forEach((sid, msgs) -> counts.put(sid, msgs.size()));
        return counts;
    }
}
