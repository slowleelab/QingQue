package com.example.customerserver.controller;

import com.example.common.model.ChatMessage;
import com.example.common.model.SenderType;
import com.example.customerserver.client.SmartcsClient;
import com.example.customerserver.message.CustomerMessageStore;
import com.example.customerserver.session.SessionManager;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.*;

@RestController
@RequestMapping("/api/sessions")
public class MessageController {
    private static final Logger log = LoggerFactory.getLogger(MessageController.class);
    private final CustomerMessageStore messageStore;
    private final SmartcsClient smartcsClient;
    private final SessionManager sessionManager;

    public MessageController(CustomerMessageStore messageStore, SmartcsClient smartcsClient,
                             SessionManager sessionManager) {
        this.messageStore = messageStore;
        this.smartcsClient = smartcsClient;
        this.sessionManager = sessionManager;
    }

    @PostMapping("/{sessionId}/messages")
    public ResponseEntity<Map<String, Object>> sendMessage(
            @PathVariable String sessionId,
            @RequestBody Map<String, String> body) {
        String sender = body.getOrDefault("sender", "customer");
        String content = body.getOrDefault("content", "");

        SenderType senderType = "agent".equals(sender) ? SenderType.AGENT : SenderType.CUSTOMER;
        ChatMessage msg = new ChatMessage(sessionId, senderType, sender, content);
        msg.setMessageId(UUID.randomUUID().toString());

        messageStore.addMessage(sessionId, msg);
        log.debug("Message stored: session={} sender={}", sessionId, sender);

        // 客户消息 → 路由到坐席 + 异步回调 SmartCS 进行 AI 分析
        if ("customer".equals(sender)) {
            // 路由消息到坐席（如果会话已分配坐席）
            sessionManager.routeMessage(sessionId, msg);

            String customerId = body.getOrDefault("customer_id", null);
            smartcsClient.analyzeMessage(sessionId, content, customerId);
        }

        Map<String, Object> resp = new LinkedHashMap<>();
        resp.put("accepted", true);
        resp.put("messageId", msg.getMessageId());
        resp.put("timestamp", msg.getTimestamp());
        return ResponseEntity.ok(resp);
    }

    /** 非阻塞读取所有消息（不删除，多端共享） */
    @GetMapping("/{sessionId}/messages")
    public ResponseEntity<List<Map<String, Object>>> getMessages(
            @PathVariable String sessionId,
            @RequestParam(defaultValue = "0") long since) {
        List<ChatMessage> pending;
        if (since > 0) {
            pending = messageStore.getMessagesSince(sessionId, since);
        } else {
            pending = messageStore.getPendingMessages(sessionId);
        }
        return ResponseEntity.ok(toResultList(pending));
    }

    /**
     * HTTP 长轮询：阻塞等待新消息，超时返回空列表。
     * 支持 since 游标参数 — 只返回 timestamp > since 的消息，多端独立轮询互不争抢。
     */
    @GetMapping("/{sessionId}/poll")
    public ResponseEntity<List<Map<String, Object>>> pollMessages(
            @PathVariable String sessionId,
            @RequestParam(defaultValue = "25000") long timeout,
            @RequestParam(defaultValue = "0") long since) {
        List<ChatMessage> messages = messageStore.pollMessages(sessionId, since, timeout);
        return ResponseEntity.ok(toResultList(messages));
    }

    private List<Map<String, Object>> toResultList(List<ChatMessage> messages) {
        List<Map<String, Object>> result = new ArrayList<>();
        for (ChatMessage m : messages) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("messageId", m.getMessageId());
            item.put("sessionId", m.getSessionId());
            item.put("sender", m.getSenderType() == SenderType.AGENT ? "agent" : "customer");
            item.put("content", m.getContent());
            item.put("timestamp", m.getTimestamp());
            result.add(item);
        }
        return result;
    }
}
