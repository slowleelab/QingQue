package com.example.customerserver.client;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * SmartCS Assist 服务客户端
 * 当 star-connection 收到客户消息时，异步回调 SmartCS 进行 AI 分析。
 */
@Service
public class SmartcsClient {
    private static final Logger log = LoggerFactory.getLogger(SmartcsClient.class);

    private final String assistUrl;
    private final RestTemplate restTemplate;

    public SmartcsClient(@Value("${smartcs.assist.url:http://localhost:8001}") String assistUrl) {
        this.assistUrl = assistUrl;
        this.restTemplate = new RestTemplate();
    }

    /**
     * 异步通知 SmartCS 分析客户消息。
     * 不阻塞主流程，失败静默。
     */
    @Async
    public void analyzeMessage(String sessionId, String message, String customerId) {
        try {
            Map<String, String> body = new LinkedHashMap<>();
            body.put("session_id", sessionId);
            body.put("message", message);
            if (customerId != null && !customerId.isEmpty()) {
                body.put("customer_id", customerId);
            }

            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);
            HttpEntity<Map<String, String>> request = new HttpEntity<>(body, headers);

            String url = assistUrl + "/api/analyze";
            String response = restTemplate.postForObject(url, request, String.class);
            log.debug("SmartCS analyze success: session={}", sessionId);
        } catch (Exception e) {
            log.warn("SmartCS analyze callback failed: session={} error={}", sessionId, e.getMessage());
        }
    }

    /**
     * 通知 SmartCS 会话状态变更。
     * 调用 /api/session/update 端点同步 SmartCS 侧的 SessionPhase。
     *
     * @param sessionId  会话 ID
     * @param phase      SmartCS 阶段 (agent / ended)
     * @param subPhase   子阶段 (agent:queued, agent:assigned, agent:active 等)，可为 null
     * @param agentId    坐席 ID，可为 null
     */
    @Async
    public void notifySessionUpdate(String sessionId, String phase, String subPhase, String agentId) {
        notifySessionUpdate(sessionId, phase, subPhase, agentId, null);
    }

    /**
     * 通知 SmartCS 会话状态变更（带 end_reason）。
     */
    @Async
    public void notifySessionUpdate(String sessionId, String phase, String subPhase,
                                     String agentId, String endReason) {
        try {
            Map<String, String> body = new LinkedHashMap<>();
            body.put("session_id", sessionId);
            body.put("phase", phase);
            if (subPhase != null && !subPhase.isEmpty()) {
                body.put("sub_phase", subPhase);
            }
            if (agentId != null && !agentId.isEmpty()) {
                body.put("agent_id", agentId);
            }
            if (endReason != null && !endReason.isEmpty()) {
                body.put("end_reason", endReason);
            }

            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);
            HttpEntity<Map<String, String>> request = new HttpEntity<>(body, headers);

            String url = assistUrl + "/api/session/update";
            String response = restTemplate.postForObject(url, request, String.class);
            log.debug("SmartCS session update success: session={} phase={}:{}", sessionId, phase, subPhase);
        } catch (Exception e) {
            log.warn("SmartCS session update callback failed: session={} phase={}:{} error={}",
                    sessionId, phase, subPhase, e.getMessage());
        }
    }
}
