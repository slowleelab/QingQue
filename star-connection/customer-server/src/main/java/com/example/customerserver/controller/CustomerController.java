package com.example.customerserver.controller;

import com.example.common.model.ChatMessage;
import com.example.common.model.SenderType;
import com.example.common.model.Session;
import com.example.common.model.SessionStatus;
import com.example.customerserver.dto.CreateSessionRequest;
import com.example.customerserver.dto.CustomerInfo;
import com.example.customerserver.dto.SessionResponse;
import com.example.customerserver.message.CustomerMessageStore;
import com.example.customerserver.session.SessionManager;
import com.example.customerserver.websocket.CustomerWebSocketHandler;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Collectors;
import java.util.UUID;

/**
 * 客户 REST API 控制器
 * 支持两种接入方式：
 * 1. WebSocket 方式 - 客户端连接 /ws/customer 进行实时双向通信
 * 2. HTTP 长轮询方式 - 客户端通过 REST API 发送和接收消息
 */
@RestController
@RequestMapping("/api/customer")
public class CustomerController {
    private static final Logger LOGGER = LoggerFactory.getLogger(CustomerController.class);

    private final SessionManager sessionManager;
    private final CustomerMessageStore messageStore;
    private final CustomerWebSocketHandler webSocketHandler;

    /**
     * 默认长轮询超时时间（毫秒）
     */
    private static final long DEFAULT_POLL_TIMEOUT = 30000;

    @Autowired
    public CustomerController(SessionManager sessionManager,
                              CustomerMessageStore messageStore,
                              CustomerWebSocketHandler webSocketHandler) {
        this.sessionManager = sessionManager;
        this.messageStore = messageStore;
        this.webSocketHandler = webSocketHandler;
    }

    /**
     * 创建会话
     */
    @PostMapping("/session")
    public ResponseEntity<SessionResponse> createSession(@RequestBody CreateSessionRequest request) {
        LOGGER.info("创建会话请求: customerId={}", request.getCustomerId());

        try {
            CustomerInfo customerInfo = request.toCustomerInfo();
            Session session = sessionManager.createSession(customerInfo);

            SessionResponse response;
            if (session.getStatus() == SessionStatus.ACTIVE) {
                response = SessionResponse.success(session.getSessionId(), session.getCustomerId(), "ACTIVE");
                response.setAgentId(session.getAgentId());
                // 获取坐席名称
                if (session.getAgentId() != null) {
                    sessionManager.getAgentName(session.getAgentId())
                            .ifPresent(response::setAgentName);
                }
            } else {
                response = SessionResponse.waiting(session.getSessionId(), session.getCustomerId());
            }
            response.setCustomerName(session.getCustomerName());
            response.setCreateTime(session.getCreateTime());

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            LOGGER.error("创建会话失败", e);
            return ResponseEntity.internalServerError()
                    .body(SessionResponse.error("创建会话失败: " + e.getMessage()));
        }
    }

    /**
     * 获取会话信息
     */
    @GetMapping("/session/{sessionId}")
    public ResponseEntity<SessionResponse> getSession(@PathVariable String sessionId) {
        Optional<Session> optionalSession = sessionManager.getSession(sessionId);
        if (optionalSession.isEmpty()) {
            return ResponseEntity.notFound().build();
        }

        Session session = optionalSession.get();
        SessionResponse response = SessionResponse.success(
                session.getSessionId(),
                session.getCustomerId(),
                session.getStatus().name()
        );
        response.setAgentId(session.getAgentId());
        response.setCustomerName(session.getCustomerName());
        response.setCreateTime(session.getCreateTime());

        return ResponseEntity.ok(response);
    }

    /**
     * 关闭会话
     */
    @DeleteMapping("/session/{sessionId}")
    public ResponseEntity<SessionResponse> closeSession(@PathVariable String sessionId) {
        LOGGER.info("关闭会话: {}", sessionId);

        Optional<Session> optionalSession = sessionManager.getSession(sessionId);
        if (optionalSession.isEmpty()) {
            return ResponseEntity.notFound().build();
        }

        sessionManager.closeSession(sessionId);
        // 清理消息队列
        messageStore.clearSession(sessionId);

        SessionResponse response = SessionResponse.success(sessionId,
                optionalSession.get().getCustomerId(), "CLOSED");
        return ResponseEntity.ok(response);
    }

    /**
     * 获取客户的活跃会话
     */
    @GetMapping("/session/customer/{customerId}")
    public ResponseEntity<SessionResponse> getCustomerSession(@PathVariable String customerId) {
        Optional<Session> optionalSession = sessionManager.getActiveSessionByCustomerId(customerId);
        if (optionalSession.isEmpty()) {
            return ResponseEntity.notFound().build();
        }

        Session session = optionalSession.get();
        SessionResponse response = SessionResponse.success(
                session.getSessionId(),
                session.getCustomerId(),
                session.getStatus().name()
        );
        response.setAgentId(session.getAgentId());
        response.setCustomerName(session.getCustomerName());
        response.setCreateTime(session.getCreateTime());

        return ResponseEntity.ok(response);
    }

    /**
     * 获取等待队列
     */
    @GetMapping("/sessions/waiting")
    public ResponseEntity<List<SessionResponse>> getWaitingSessions() {
        List<Session> sessions = sessionManager.getWaitingSessions();
        List<SessionResponse> responses = sessions.stream()
                .map(this::toSessionResponse)
                .collect(Collectors.toList());
        return ResponseEntity.ok(responses);
    }

    /**
     * 获取统计信息
     */
    @GetMapping("/stats")
    public ResponseEntity<Map<String, Object>> getStats() {
        Map<String, Object> stats = new HashMap<>();
        stats.put("waitingSessions", sessionManager.getWaitingSessions().size());
        return ResponseEntity.ok(stats);
    }

    // ========== HTTP 长轮询接口 ==========

    /**
     * 发送消息（HTTP 方式）
     * 客户端通过此接口发送消息，消息将被路由到坐席
     */
    @PostMapping("/session/{sessionId}/message")
    public ResponseEntity<Map<String, Object>> sendMessage(
            @PathVariable String sessionId,
            @RequestBody Map<String, Object> request) {

        Optional<Session> optionalSession = sessionManager.getSession(sessionId);
        if (optionalSession.isEmpty()) {
            return ResponseEntity.notFound().build();
        }

        Session session = optionalSession.get();
        if (session.getStatus() == SessionStatus.CLOSED) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "会话已关闭"));
        }

        String content = (String) request.get("content");
        if (content == null || content.trim().isEmpty()) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "消息内容不能为空"));
        }

        String senderId = (String) request.getOrDefault("senderId", session.getCustomerId());
        String senderName = (String) request.getOrDefault("senderName", session.getCustomerName());

        // 创建聊天消息
        ChatMessage chatMessage = new ChatMessage();
        chatMessage.setMessageId(UUID.randomUUID().toString());
        chatMessage.setSessionId(sessionId);
        chatMessage.setSenderType(SenderType.CUSTOMER);
        chatMessage.setSenderId(senderId);
        chatMessage.setSenderName(senderName);
        chatMessage.setContent(content);
        chatMessage.setContentType("text");
        chatMessage.setTimestamp(System.currentTimeMillis());

        // 路由消息到坐席
        sessionManager.routeMessage(sessionId, chatMessage);

        LOGGER.info("客户消息已发送: sessionId={}, messageId={}", sessionId, chatMessage.getMessageId());

        return ResponseEntity.ok(Map.of(
                "success", true,
                "messageId", chatMessage.getMessageId(),
                "timestamp", chatMessage.getTimestamp()
        ));
    }

    /**
     * 长轮询获取消息（HTTP 方式）
     * 客户端通过此接口接收消息，使用长轮询机制
     *
     * @param sessionId 会话ID
     * @param timeout 超时时间（毫秒），默认30秒
     * @return 消息列表，可能为空（超时时返回空列表）
     */
    @GetMapping("/session/{sessionId}/messages")
    public ResponseEntity<Map<String, Object>> pollMessages(
            @PathVariable String sessionId,
            @RequestParam(defaultValue = "30000") long timeout) {

        Optional<Session> optionalSession = sessionManager.getSession(sessionId);
        if (optionalSession.isEmpty()) {
            return ResponseEntity.notFound().build();
        }

        Session session = optionalSession.get();
        if (session.getStatus() == SessionStatus.CLOSED) {
            Map<String, Object> response = new HashMap<>();
            response.put("sessionId", sessionId);
            response.put("status", "CLOSED");
            response.put("messages", List.of());
            return ResponseEntity.ok(response);
        }

        // 长轮询获取消息
        List<ChatMessage> messages = messageStore.pollMessages(sessionId, timeout);

        LOGGER.debug("长轮询返回: sessionId={}, messageCount={}", sessionId, messages.size());

        Map<String, Object> response = new HashMap<>();
        response.put("sessionId", sessionId);
        response.put("status", session.getStatus().name());
        response.put("agentId", session.getAgentId());
        response.put("messages", messages);
        return ResponseEntity.ok(response);
    }

    /**
     * 非阻塞获取待处理消息
     * 客户端可以定期调用此接口检查是否有新消息
     */
    @GetMapping("/session/{sessionId}/messages/pending")
    public ResponseEntity<Map<String, Object>> getPendingMessages(@PathVariable String sessionId) {
        Optional<Session> optionalSession = sessionManager.getSession(sessionId);
        if (optionalSession.isEmpty()) {
            return ResponseEntity.notFound().build();
        }

        Session session = optionalSession.get();
        List<ChatMessage> messages = messageStore.getPendingMessages(sessionId);
        int pendingCount = messageStore.getPendingCount(sessionId);

        Map<String, Object> response = new HashMap<>();
        response.put("sessionId", sessionId);
        response.put("status", session.getStatus().name());
        response.put("pendingCount", pendingCount);
        response.put("messages", messages);
        return ResponseEntity.ok(response);
    }

    /**
     * 检查会话状态
     * 客户端可以定期调用此接口检查会话是否已分配坐席或已关闭
     */
    @GetMapping("/session/{sessionId}/status")
    public ResponseEntity<Map<String, Object>> checkStatus(@PathVariable String sessionId) {
        Optional<Session> optionalSession = sessionManager.getSession(sessionId);
        if (optionalSession.isEmpty()) {
            return ResponseEntity.notFound().build();
        }

        Session session = optionalSession.get();
        Map<String, Object> response = new HashMap<>();
        response.put("sessionId", sessionId);
        response.put("status", session.getStatus().name());
        response.put("agentId", session.getAgentId());
        response.put("customerId", session.getCustomerId());

        if (session.getAgentId() != null) {
            sessionManager.getAgentName(session.getAgentId())
                    .ifPresent(name -> response.put("agentName", name));
        }

        response.put("createTime", session.getCreateTime());
        response.put("updateTime", session.getUpdateTime());
        response.put("pendingMessages", messageStore.getPendingCount(sessionId));

        return ResponseEntity.ok(response);
    }

    private SessionResponse toSessionResponse(Session session) {
        SessionResponse response = SessionResponse.success(
                session.getSessionId(),
                session.getCustomerId(),
                session.getStatus().name()
        );
        response.setAgentId(session.getAgentId());
        response.setCustomerName(session.getCustomerName());
        response.setCreateTime(session.getCreateTime());
        return response;
    }
}
