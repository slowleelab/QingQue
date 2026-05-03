package com.example.agentserver.controller;

import com.example.agentserver.config.AgentServerProperties;
import com.example.agentserver.config.NettyAgentServerProperties;
import com.example.agentserver.dto.ClientStatusResponse;
import com.example.agentserver.netty.manager.ConnectionManager;
import com.example.agentserver.zookeeper.ServiceRegistry;
import com.example.agentserver.websocket.AgentWebSocketHandler;
import org.apache.curator.x.discovery.ServiceInstance;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.HashMap;
import java.util.Map;

/**
 * 客户端监控 REST API 控制器
 */
@RestController
@RequestMapping("/api/monitor")
public class MonitorController {

    private final ConnectionManager connectionManager;
    private final ServiceRegistry serviceRegistry;
    private final AgentServerProperties clientProperties;
    private final NettyAgentServerProperties nettyAgentServerProperties;
    private final AgentWebSocketHandler agentWebSocketHandler;

    @Autowired
    public MonitorController(ConnectionManager connectionManager,
                            ServiceRegistry serviceRegistry,
                            AgentServerProperties clientProperties,
                            NettyAgentServerProperties nettyAgentServerProperties,
                            AgentWebSocketHandler agentWebSocketHandler) {
        this.connectionManager = connectionManager;
        this.serviceRegistry = serviceRegistry;
        this.clientProperties = clientProperties;
        this.nettyAgentServerProperties = nettyAgentServerProperties;
        this.agentWebSocketHandler = agentWebSocketHandler;
    }

    /**
     * 获取客户端状态
     */
    @GetMapping("/status")
    public ResponseEntity<ClientStatusResponse> getStatus() {
        ClientStatusResponse response = new ClientStatusResponse();

        // 基本信息
        response.setServiceId(clientProperties.getServiceId());
        response.setServiceName(clientProperties.getServiceName());

        // 连接状态
        ClientStatusResponse.ConnectionStatus connStatus = new ClientStatusResponse.ConnectionStatus();
        connStatus.setConnected(connectionManager.isConnected());
        connStatus.setServerHost(nettyAgentServerProperties.getHost());
        connStatus.setServerPort(nettyAgentServerProperties.getPort());
        connStatus.setStatus(connectionManager.isConnected() ? "CONNECTED" : "DISCONNECTED");
        response.setConnection(connStatus);

        // 注册状态
        ClientStatusResponse.RegistrationStatus regStatus = new ClientStatusResponse.RegistrationStatus();
        regStatus.setRegistered(serviceRegistry.isRegistered());

        ServiceInstance<Map<String, String>> instance = serviceRegistry.getServiceInstance();
        if (instance != null && instance.getPayload() != null) {
            regStatus.setMetadata(instance.getPayload());
        } else {
            regStatus.setMetadata(new HashMap<>());
        }

        response.setRegistration(regStatus);

        return ResponseEntity.ok(response);
    }

    /**
     * 获取健康检查状态
     */
    @GetMapping("/health")
    public ResponseEntity<Map<String, Object>> getHealth() {
        Map<String, Object> health = new HashMap<>();
        health.put("serviceId", clientProperties.getServiceId());
        health.put("netty", connectionManager.isConnected() ? "CONNECTED" : "DISCONNECTED");
        health.put("zookeeper", serviceRegistry.isRegistered() ? "REGISTERED" : "NOT_REGISTERED");
        health.put("connectedRouters", connectionManager.getConnectedRouterCount());
        health.put("routerIds", connectionManager.getConnectedRouterIds());
        health.put("webSocketConnections", agentWebSocketHandler.getConnectionCount());
        return ResponseEntity.ok(health);
    }
}
