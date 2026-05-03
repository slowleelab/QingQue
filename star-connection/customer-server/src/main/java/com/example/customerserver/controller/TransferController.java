package com.example.customerserver.controller;

import com.example.common.model.Session;
import com.example.common.model.SessionStatus;
import com.example.customerserver.dto.TransferSessionRequest;
import com.example.customerserver.dto.TransferSessionResponse;
import com.example.customerserver.session.SessionStore;
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
    private final SessionStore sessionStore;

    public TransferController(SessionStore sessionStore) {
        this.sessionStore = sessionStore;
    }

    @PostMapping("/sessions")
    public ResponseEntity<TransferSessionResponse> createSession(
            @RequestBody TransferSessionRequest request
    ) {
        String sessionId = request.getSessionId();
        if (sessionId == null || sessionId.isEmpty()) {
            sessionId = UUID.randomUUID().toString();
        }

        log.info("Creating transfer session: sessionId={}, reason={}", sessionId, request.getTransferReason());

        Session session = new Session(sessionId, request.getCustomerId());
        session.setCustomerName(request.getCustomerId());
        session.setStatus(SessionStatus.WAITING);
        sessionStore.save(session);

        String token = Base64.getUrlEncoder().encodeToString(
            (sessionId + ":" + System.currentTimeMillis()).getBytes()
        );

        String pollUrl = "http://localhost:8080/customer/poll?session_id=" + sessionId + "&token=" + token;
        String sendUrl = "http://localhost:8080/customer/send";

        return ResponseEntity.ok(new TransferSessionResponse(sessionId, pollUrl, sendUrl, token));
    }
}
