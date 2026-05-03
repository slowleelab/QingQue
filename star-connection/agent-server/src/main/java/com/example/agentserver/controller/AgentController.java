package com.example.agentserver.controller;

import com.example.agentserver.agent.AgentManager;
import com.example.agentserver.agent.AgentSessionRegistry;
import com.example.common.model.AgentStatus;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 坐席 REST API 控制器
 */
@RestController
@RequestMapping("/api/agent")
public class AgentController {
    private static final Logger LOGGER = LoggerFactory.getLogger(AgentController.class);

    private final AgentManager agentManager;
    private final AgentSessionRegistry sessionRegistry;

    @Autowired
    public AgentController(AgentManager agentManager, AgentSessionRegistry sessionRegistry) {
        this.agentManager = agentManager;
        this.sessionRegistry = sessionRegistry;
    }

    /**
     * 获取坐席信息
     */
    @GetMapping("/{agentId}")
    public ResponseEntity<Map<String, Object>> getAgentInfo(@PathVariable String agentId) {
        AgentManager.AgentInfo agentInfo = agentManager.getAgentInfo(agentId);
        if (agentInfo == null) {
            return ResponseEntity.notFound().build();
        }

        Map<String, Object> response = new HashMap<>();
        response.put("agentId", agentInfo.getAgentId());
        response.put("agentName", agentInfo.getAgentName());
        response.put("status", agentInfo.getStatus().name());
        response.put("maxSessions", agentInfo.getMaxSessions());
        response.put("currentSessions", agentInfo.getCurrentSessions());
        response.put("onlineTime", agentInfo.getOnlineTime());

        return ResponseEntity.ok(response);
    }

    /**
     * 更新坐席状态
     */
    @PutMapping("/{agentId}/status")
    public ResponseEntity<Map<String, Object>> updateStatus(
            @PathVariable String agentId,
            @RequestBody Map<String, String> request) {

        String statusStr = request.get("status");
        if (statusStr == null) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "缺少 status 参数"));
        }

        try {
            AgentStatus status = AgentStatus.valueOf(statusStr.toUpperCase());
            agentManager.updateAgentStatus(agentId, status);

            Map<String, Object> response = new HashMap<>();
            response.put("agentId", agentId);
            response.put("status", status.name());

            return ResponseEntity.ok(response);
        } catch (IllegalArgumentException e) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "无效的状态值: " + statusStr));
        }
    }

    /**
     * 获取坐席的会话列表
     */
    @GetMapping("/{agentId}/sessions")
    public ResponseEntity<Map<String, Object>> getAgentSessions(@PathVariable String agentId) {
        List<String> sessions = sessionRegistry.getSessions(agentId);

        Map<String, Object> response = new HashMap<>();
        response.put("agentId", agentId);
        response.put("sessions", sessions);
        response.put("count", sessions.size());

        return ResponseEntity.ok(response);
    }

    /**
     * 检查坐席是否在线
     */
    @GetMapping("/{agentId}/online")
    public ResponseEntity<Map<String, Object>> checkOnline(@PathVariable String agentId) {
        boolean online = agentManager.isAgentOnline(agentId);

        Map<String, Object> response = new HashMap<>();
        response.put("agentId", agentId);
        response.put("online", online);

        return ResponseEntity.ok(response);
    }

    /**
     * 获取统计信息
     */
    @GetMapping("/stats")
    public ResponseEntity<Map<String, Object>> getStats() {
        Map<String, Object> stats = new HashMap<>();
        stats.put("onlineAgents", sessionRegistry.getOnlineAgents().size());
        stats.put("totalSessions", sessionRegistry.getOnlineAgents().stream()
                .mapToInt(sessionRegistry::getSessionCount)
                .sum());
        return ResponseEntity.ok(stats);
    }
}
