package com.example.customerserver.controller;

import com.example.customerserver.dto.ConnectionStatus;
import com.example.customerserver.dto.CustomerServiceStats;
import com.example.customerserver.dto.MonitorStatusResponse;
import com.example.customerserver.dto.NodeMetrics;
import com.example.customerserver.dto.PageResponse;
import com.example.customerserver.dto.ServiceInfo;
import com.example.customerserver.dto.SessionInfo;
import com.example.customerserver.dto.SessionQueryRequest;
import com.example.customerserver.dto.ZookeeperMetadata;
import com.example.customerserver.dto.CustomerServiceStats.AgentInfo;
import com.example.customerserver.service.MonitorService;
import com.example.customerserver.service.MonitorService.FrontendNodeInfo;
import com.example.customerserver.service.MonitorService.BackendNodeInfo;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 监控 REST API 控制器
 */
@RestController
@RequestMapping("/api/monitor")
public class MonitorController {

    private final MonitorService monitorService;

    @Autowired
    public MonitorController(MonitorService monitorService) {
        this.monitorService = monitorService;
    }

    /**
     * 获取综合状态
     */
    @GetMapping("/status")
    public ResponseEntity<MonitorStatusResponse> getStatus() {
        return ResponseEntity.ok(monitorService.getStatus());
    }

    /**
     * 获取连接详情列表
     */
    @GetMapping("/connections")
    public ResponseEntity<List<ConnectionStatus>> getConnections() {
        return ResponseEntity.ok(monitorService.getConnectionDetails());
    }

    /**
     * 获取 ZooKeeper 服务列表
     */
    @GetMapping("/services")
    public ResponseEntity<List<ServiceInfo>> getServices() {
        return ResponseEntity.ok(monitorService.getServices());
    }

    /**
     * 获取健康检查状态
     */
    @GetMapping("/health")
    public ResponseEntity<Map<String, Object>> getHealth() {
        Map<String, Object> health = new HashMap<>();
        health.put("netty", monitorService.getStatus().getServer().getStatus());
        health.put("zookeeper", monitorService.isZookeeperConnected() ? "CONNECTED" : "DISCONNECTED");
        health.put("nettyConnections", monitorService.getConnectionDetails().size());
        health.put("webSocketConnections", monitorService.getCustomerWebSocketCount());
        health.put("nettyPort", monitorService.getNettyPort());

        // 添加启动时间和运行时长
        FrontendNodeInfo nodeInfo = monitorService.getFrontendNodeInfo();
        health.put("startTime", nodeInfo.getStartTime());
        health.put("uptimeMillis", nodeInfo.getUptimeMillis());

        return ResponseEntity.ok(health);
    }

    /**
     * 获取客户 WebSocket 连接数
     */
    @GetMapping("/websocket/connections")
    public ResponseEntity<Map<String, Object>> getWebSocketConnections() {
        Map<String, Object> result = new HashMap<>();
        result.put("count", monitorService.getCustomerWebSocketCount());
        return ResponseEntity.ok(result);
    }

    // ========== 在线客服系统监控接口 ==========

    /**
     * 获取客服系统统计
     */
    @GetMapping("/customer-service/stats")
    public ResponseEntity<CustomerServiceStats> getCustomerServiceStats() {
        return ResponseEntity.ok(monitorService.getCustomerServiceStats());
    }

    /**
     * 获取活跃会话列表
     */
    @GetMapping("/customer-service/sessions")
    public ResponseEntity<List<SessionInfo>> getActiveSessions() {
        return ResponseEntity.ok(monitorService.getActiveSessions());
    }

    /**
     * 获取坐席列表
     */
    @GetMapping("/customer-service/agents")
    public ResponseEntity<List<AgentInfo>> getAgents() {
        return ResponseEntity.ok(monitorService.getAgents());
    }

    /**
     * 查询会话列表（支持分页和条件筛选）
     */
    @GetMapping("/customer-service/sessions/query")
    public ResponseEntity<PageResponse<SessionInfo>> querySessions(
            @RequestParam(required = false) String sessionId,
            @RequestParam(required = false) String customerId,
            @RequestParam(required = false) String agentId,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) Long startTime,
            @RequestParam(required = false) Long endTime,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "10") int size) {

        SessionQueryRequest request = new SessionQueryRequest();
        request.setSessionId(sessionId);
        request.setCustomerId(customerId);
        request.setAgentId(agentId);
        request.setStatus(status);
        request.setStartTime(startTime);
        request.setEndTime(endTime);
        request.setPage(page);
        request.setSize(size);

        return ResponseEntity.ok(monitorService.querySessions(request));
    }

    /**
     * 获取会话详情
     */
    @GetMapping("/customer-service/sessions/{sessionId}")
    public ResponseEntity<SessionInfo> getSessionDetail(@PathVariable String sessionId) {
        SessionInfo session = monitorService.getSessionDetail(sessionId);
        if (session == null) {
            return ResponseEntity.notFound().build();
        }
        return ResponseEntity.ok(session);
    }

    /**
     * 获取最近N条会话
     */
    @GetMapping("/customer-service/sessions/recent")
    public ResponseEntity<List<SessionInfo>> getRecentSessions(
            @RequestParam(defaultValue = "10") int limit) {
        return ResponseEntity.ok(monitorService.getRecentSessions(limit));
    }

    // ========== 节点监控接口 ==========

    /**
     * 获取所有节点指标
     */
    @GetMapping("/nodes/metrics")
    public ResponseEntity<List<NodeMetrics>> getAllNodeMetrics() {
        return ResponseEntity.ok(monitorService.getAllNodeMetrics());
    }

    /**
     * 获取当前前端节点指标
     */
    @GetMapping("/nodes/frontend/metrics")
    public ResponseEntity<FrontendNodeInfo> getFrontendMetrics() {
        return ResponseEntity.ok(monitorService.getFrontendNodeInfo());
    }

    /**
     * 获取所有前端节点信息
     */
    @GetMapping("/nodes/frontends")
    public ResponseEntity<List<FrontendNodeInfo>> getAllFrontendNodes() {
        return ResponseEntity.ok(monitorService.getAllFrontendNodes());
    }

    /**
     * 获取所有坐席后台节点信息
     */
    @GetMapping("/nodes/backends")
    public ResponseEntity<List<BackendNodeInfo>> getAllBackendNodes() {
        return ResponseEntity.ok(monitorService.getAllBackendNodes());
    }

    /**
     * 获取ZooKeeper元数据
     */
    @GetMapping("/zookeeper/metadata")
    public ResponseEntity<ZookeeperMetadata> getZookeeperMetadata() {
        return ResponseEntity.ok(monitorService.getZookeeperMetadata());
    }
}
