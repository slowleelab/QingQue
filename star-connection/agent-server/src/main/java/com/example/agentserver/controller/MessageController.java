package com.example.agentserver.controller;

import com.example.agentserver.config.AgentServerProperties;
import com.example.agentserver.netty.handler.ClientMessageHandler;
import com.example.common.model.Message;
import com.example.common.model.MessageType;
import com.example.common.util.MessageIdGenerator;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

/**
 * REST控制器，用于发送测试消息
 */
@RestController
@RequestMapping("/api/messages")
public class MessageController {
    private static final Logger LOGGER = LoggerFactory.getLogger(MessageController.class);

    private final ClientMessageHandler messageHandler;
    private final AgentServerProperties clientProperties;

    @Autowired
    public MessageController(ClientMessageHandler messageHandler, AgentServerProperties clientProperties) {
        this.messageHandler = messageHandler;
        this.clientProperties = clientProperties;
    }

    /**
     * 向其他服务发送请求消息
     */
    @PostMapping("/request")
    public ResponseEntity<Map<String, Object>> sendRequest(
            @RequestBody SendMessageRequest request) {

        if (request.getTarget() == null || request.getTarget().isEmpty()) {
            return ResponseEntity.badRequest().body(Map.of(
                    "error", "目标服务是必需的"
            ));
        }

        try {
            Message message = new Message(MessageType.REQUEST,
                    clientProperties.getServiceId(),
                    request.getTarget());
            message.setMessageId(MessageIdGenerator.generate());

            if (request.getPayload() != null) {
                message.setPayload(request.getPayload());
            }

            if (request.getHeaders() != null) {
                request.getHeaders().forEach(message::addHeader);
            }

            if (request.isWaitForResponse()) {
                // 发送并等待响应
                CompletableFuture<Message> responseFuture = new CompletableFuture<>();

                messageHandler.sendRequest(message, response -> {
                    responseFuture.complete(response);
                });

                // 等待响应，带超时
                Message response;
                try {
                    response = responseFuture.get(30, TimeUnit.SECONDS);
                } catch (Exception e) {
                    return ResponseEntity.ok(Map.of(
                            "status", "timeout",
                            "messageId", message.getMessageId(),
                            "error", "响应超时"
                    ));
                }

                return ResponseEntity.ok(Map.of(
                        "status", "success",
                        "messageId", message.getMessageId(),
                        "response", Map.of(
                                "messageId", response.getMessageId(),
                                "headers", response.getHeaders(),
                                "payload", response.getPayload()
                        )
                ));
            } else {
                // 发送不等待响应
                messageHandler.sendRequest(message);
                return ResponseEntity.ok(Map.of(
                        "status", "sent",
                        "messageId", message.getMessageId()
                ));
            }
        } catch (Exception e) {
            LOGGER.error("发送请求失败", e);
            return ResponseEntity.internalServerError().body(Map.of(
                    "error", "发送消息失败: " + e.getMessage()
            ));
        }
    }

    /**
     * 发送通知消息
     */
    @PostMapping("/notify")
    public ResponseEntity<Map<String, Object>> sendNotification(
            @RequestBody SendMessageRequest request) {

        if (request.getTarget() == null || request.getTarget().isEmpty()) {
            return ResponseEntity.badRequest().body(Map.of(
                    "error", "目标服务是必需的"
            ));
        }

        try {
            Message message = new Message(MessageType.NOTIFY,
                    clientProperties.getServiceId(),
                    request.getTarget());
            message.setMessageId(MessageIdGenerator.generate());

            if (request.getPayload() != null) {
                message.setPayload(request.getPayload());
            }

            if (request.getHeaders() != null) {
                request.getHeaders().forEach(message::addHeader);
            }

            messageHandler.sendNotification(message);

            return ResponseEntity.ok(Map.of(
                    "status", "sent",
                    "messageId", message.getMessageId()
            ));
        } catch (Exception e) {
            LOGGER.error("发送通知失败", e);
            return ResponseEntity.internalServerError().body(Map.of(
                    "error", "发送通知失败: " + e.getMessage()
            ));
        }
    }

    /**
     * 获取客户端状态
     */
    @GetMapping("/status")
    public ResponseEntity<Map<String, Object>> getStatus() {
        return ResponseEntity.ok(Map.of(
                "status", "ok",
                "timestamp", System.currentTimeMillis()
        ));
    }

    /**
     * 请求DTO
     */
    public static class SendMessageRequest {
        private String target;
        private String payload;
        private Map<String, String> headers;
        private boolean waitForResponse = false;

        // 获取器和设置器
        public String getTarget() {
            return target;
        }

        public void setTarget(String target) {
            this.target = target;
        }

        public String getPayload() {
            return payload;
        }

        public void setPayload(String payload) {
            this.payload = payload;
        }

        public Map<String, String> getHeaders() {
            return headers;
        }

        public void setHeaders(Map<String, String> headers) {
            this.headers = headers;
        }

        public boolean isWaitForResponse() {
            return waitForResponse;
        }

        public void setWaitForResponse(boolean waitForResponse) {
            this.waitForResponse = waitForResponse;
        }
    }
}