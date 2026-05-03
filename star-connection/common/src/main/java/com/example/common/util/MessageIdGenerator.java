package com.example.common.util;

import java.util.UUID;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 消息ID生成器
 */
public class MessageIdGenerator {
    private static final AtomicLong SEQUENCE = new AtomicLong(0);
    private static final String NODE_ID = generateNodeId();

    /**
     * 生成唯一消息ID
     */
    public static String generate() {
        long sequence = SEQUENCE.incrementAndGet() & 0xFFFF;
        long timestamp = System.currentTimeMillis();
        return String.format("%s-%d-%04x", NODE_ID, timestamp, sequence);
    }

    /**
     * 生成节点ID
     */
    private static String generateNodeId() {
        String uuid = UUID.randomUUID().toString().replace("-", "");
        return uuid.substring(0, 8);
    }

    /**
     * 生成带前缀的消息ID
     */
    public static String generate(String prefix) {
        return prefix + "-" + generate();
    }
}