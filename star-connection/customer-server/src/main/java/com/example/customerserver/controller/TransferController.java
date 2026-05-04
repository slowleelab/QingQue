package com.example.customerserver.controller;

import com.example.common.model.Agent;
import com.example.common.model.AgentStatus;
import com.example.common.model.Session;
import com.example.common.model.SessionStatus;
import com.example.customerserver.agent.AgentRegistry;
import com.example.customerserver.dto.CustomerInfo;
import com.example.customerserver.dto.TransferSessionRequest;
import com.example.customerserver.dto.TransferSessionResponse;
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

    public TransferController(SessionManager sessionManager, AgentRegistry agentRegistry) {
        this.sessionManager = sessionManager;
        this.agentRegistry = agentRegistry;
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
        log.info("Session {} status={} agent={}", session.getSessionId(), status, session.getAgentId());

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
