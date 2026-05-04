package com.example.customerserver.controller;

import com.example.common.model.Agent;
import com.example.common.model.AgentStatus;
import com.example.common.model.ChatMessage;
import com.example.common.model.SenderType;
import com.example.common.model.Session;
import com.example.common.model.SessionStatus;
import com.example.customerserver.agent.AgentRegistry;
import com.example.customerserver.dto.CustomerInfo;
import com.example.customerserver.dto.TransferSessionRequest;
import com.example.customerserver.dto.TransferSessionResponse;
import com.example.customerserver.message.CustomerMessageStore;
import com.example.customerserver.session.SessionManager;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Base64;
import java.util.UUID;

@RestController
@RequestMapping("/api")
public class TransferController {

    private static final Logger log = LoggerFactory.getLogger(TransferController.class);
    private final SessionManager sessionManager;
    private final AgentRegistry agentRegistry;
    private final CustomerMessageStore messageStore;

    public TransferController(SessionManager sessionManager, AgentRegistry agentRegistry,
                              CustomerMessageStore messageStore) {
        this.sessionManager = sessionManager;
        this.agentRegistry = agentRegistry;
        this.messageStore = messageStore;
    }

    @PostMapping("/sessions")
    public ResponseEntity<TransferSessionResponse> createSession(
            @RequestBody TransferSessionRequest request
    ) {
        String smartcsSessionId = request.getSessionId();
        if (smartcsSessionId == null || smartcsSessionId.isEmpty()) {
            smartcsSessionId = UUID.randomUUID().toString();
        }

        log.info("Creating transfer session from SmartCS: sessionId={}", smartcsSessionId);

        // 确保有一个默认坐席可用（开发/演示环境）
        ensureDemoAgent();

        // 使用 SessionManager 创建会话（自动触发坐席分配）
        CustomerInfo customerInfo = new CustomerInfo(
            request.getCustomerId() != null ? request.getCustomerId() : "cust-" + smartcsSessionId.substring(0, 8),
            "客户"
        );
        customerInfo.setSource("SMARTCS_BOT");

        Session session = sessionManager.createSession(customerInfo);

        String status = session.getStatus() == SessionStatus.ACTIVE ? "ACTIVE" : "WAITING";
        String agentName = session.getAgentId() != null ? "坐席" : null;
        log.info("Session {} status={} agent={}", session.getSessionId(), status, session.getAgentId());

        // 发送坐席欢迎语
        if (session.getAgentId() != null) {
            Agent agent = agentRegistry.findById(session.getAgentId()).orElse(null);
            agentName = agent != null ? agent.getAgentName() : "客服";
            String summary = request.getTransferSummary();
            if (summary == null || summary.isEmpty()) {
                summary = request.getTransferReason();
            }
            if (summary == null || summary.isEmpty()) {
                summary = "转人工请求";
            }
            String welcome = "您好，我是" + agentName + "，已收到您的问题，正在为您处理。";
            ChatMessage welcomeMsg = new ChatMessage(
                session.getSessionId(), SenderType.AGENT, session.getAgentId(), welcome
            );
            welcomeMsg.setMessageId(UUID.randomUUID().toString());
            welcomeMsg.setSenderName(agentName);
            messageStore.addMessage(session.getSessionId(), welcomeMsg);
            log.info("Sent agent welcome message for session {}", session.getSessionId());
        }

        String token = Base64.getUrlEncoder().encodeToString(
            (session.getSessionId() + ":" + System.currentTimeMillis()).getBytes()
        );

        String pollUrl = "http://localhost:8080/customer/poll?session_id=" + session.getSessionId() + "&token=" + token;
        String sendUrl = "http://localhost:8080/customer/send";

        return ResponseEntity.ok(new TransferSessionResponse(
            session.getSessionId(), pollUrl, sendUrl, token
        ));
    }

    private void ensureDemoAgent() {
        if (agentRegistry.getAvailableAgents().isEmpty()) {
            Agent demoAgent = new Agent("agent-1", "王客服");
            demoAgent.setStatus(AgentStatus.ONLINE);
            demoAgent.setMaxSessions(10);
            demoAgent.setCurrentSessions(0);
            demoAgent.setBackendId("backend-1");
            agentRegistry.registerAgent(demoAgent);
            log.info("Registered demo agent: agent-1 (王客服)");
        }
    }
}
