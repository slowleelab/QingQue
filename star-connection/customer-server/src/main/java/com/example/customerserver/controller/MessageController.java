package com.example.customerserver.controller;

import com.example.common.model.ChatMessage;
import com.example.common.model.SenderType;
import com.example.customerserver.message.CustomerMessageStore;
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

    public MessageController(CustomerMessageStore messageStore) {
        this.messageStore = messageStore;
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

        Map<String, Object> resp = new HashMap<>();
        resp.put("accepted", true);
        resp.put("messageId", msg.getMessageId());
        return ResponseEntity.ok(resp);
    }

    /** 非阻塞读取（不删除，多端共享） */
    @GetMapping("/{sessionId}/messages")
    public ResponseEntity<List<Map<String, Object>>> getMessages(@PathVariable String sessionId) {
        List<ChatMessage> pending = messageStore.getPendingMessages(sessionId);
        List<Map<String, Object>> result = new ArrayList<>();
        for (ChatMessage m : pending) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("messageId", m.getMessageId());
            item.put("sessionId", m.getSessionId());
            item.put("sender", m.getSenderType() == SenderType.AGENT ? "agent" : "customer");
            item.put("content", m.getContent());
            item.put("timestamp", m.getTimestamp());
            result.add(item);
        }
        return ResponseEntity.ok(result);
    }

    /** HTTP 长轮询：阻塞等待新消息，超时返回空列表 */
    @GetMapping("/{sessionId}/poll")
    public ResponseEntity<List<Map<String, Object>>> pollMessages(
            @PathVariable String sessionId,
            @RequestParam(defaultValue = "25000") long timeout) {
        List<ChatMessage> messages = messageStore.pollMessages(sessionId, timeout);
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
        return ResponseEntity.ok(result);
    }
}
