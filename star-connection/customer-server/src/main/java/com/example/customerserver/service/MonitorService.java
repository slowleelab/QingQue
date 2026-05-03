package com.example.customerserver.service;

import com.example.customerserver.agent.AgentRegistry;
import com.example.customerserver.dto.ConnectionStatus;
import com.example.customerserver.dto.CustomerServiceStats;
import com.example.customerserver.dto.CustomerServiceStats.AgentInfo;
import com.example.customerserver.dto.MonitorStatusResponse;
import com.example.customerserver.dto.NodeMetrics;
import com.example.customerserver.dto.PageResponse;
import com.example.customerserver.dto.ServerStatus;
import com.example.customerserver.dto.ServiceInfo;
import com.example.customerserver.dto.SessionInfo;
import com.example.customerserver.dto.SessionQueryRequest;
import com.example.customerserver.dto.ZookeeperMetadata;
import com.example.customerserver.netty.NettyServer;
import com.example.customerserver.netty.handler.AuthHandler;
import com.example.customerserver.netty.manager.ConnectionManager;
import com.example.customerserver.session.SessionStore;
import com.example.customerserver.websocket.CustomerWebSocketHandler;
import com.example.customerserver.zookeeper.RouterServiceDiscovery;
import com.example.common.model.Agent;
import com.example.common.model.AgentStatus;
import com.example.common.model.Session;
import com.example.common.model.SessionStatus;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.curator.x.discovery.ServiceInstance;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.lang.management.ManagementFactory;
import java.lang.management.MemoryMXBean;
import java.lang.management.OperatingSystemMXBean;
import java.lang.management.ThreadMXBean;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.text.SimpleDateFormat;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * 监控服务，聚合监控数据
 */
@Service
public class MonitorService {
    private static final Logger LOGGER = LoggerFactory.getLogger(MonitorService.class);

    private final NettyServer nettyServer;
    private final ConnectionManager connectionManager;
    private final AuthHandler authHandler;
    private final RouterServiceDiscovery serviceDiscovery;
    private final SessionStore sessionStore;
    private final AgentRegistry agentRegistry;
    private final CustomerWebSocketHandler customerWebSocketHandler;

    @Value("${router.service-id:frontend-1}")
    private String serviceId;

    @Value("${server.port:8080}")
    private int httpPort;

    @Autowired
    public MonitorService(NettyServer nettyServer,
                          ConnectionManager connectionManager,
                          AuthHandler authHandler,
                          RouterServiceDiscovery serviceDiscovery,
                          SessionStore sessionStore,
                          AgentRegistry agentRegistry,
                          CustomerWebSocketHandler customerWebSocketHandler) {
        this.nettyServer = nettyServer;
        this.connectionManager = connectionManager;
        this.authHandler = authHandler;
        this.serviceDiscovery = serviceDiscovery;
        this.sessionStore = sessionStore;
        this.agentRegistry = agentRegistry;
        this.customerWebSocketHandler = customerWebSocketHandler;
    }

    /**
     * 获取综合监控状态
     */
    public MonitorStatusResponse getStatus() {
        MonitorStatusResponse response = new MonitorStatusResponse();

        // 服务器状态
        ServerStatus serverStatus = new ServerStatus();
        serverStatus.setPort(nettyServer.getPort());
        serverStatus.setStatus(nettyServer.isRunning() ? "RUNNING" : "STOPPED");
        serverStatus.setStartTime(nettyServer.getStartTime());
        if (nettyServer.getStartTime() > 0) {
            serverStatus.setUptimeMillis(System.currentTimeMillis() - nettyServer.getStartTime());
        }
        response.setServer(serverStatus);

        // 连接统计
        MonitorStatusResponse.ConnectionStatistics connStats = new MonitorStatusResponse.ConnectionStatistics();
        connStats.setActiveCount(connectionManager.getConnectionCount());
        connStats.setAuthenticatedCount(authHandler.getAuthenticatedChannelCount());
        connStats.setZkServiceCount(serviceDiscovery.getRegisteredServiceCount());
        response.setConnections(connStats);

        return response;
    }

    /**
     * 获取连接详情列表
     */
    public List<ConnectionStatus> getConnectionDetails() {
        return connectionManager.getConnectionDetails();
    }

    /**
     * 获取 ZooKeeper 服务列表
     */
    public List<ServiceInfo> getServices() {
        List<ServiceInfo> services = new ArrayList<>();
        try {
            Collection<ServiceInstance<Map<String, String>>> instances = serviceDiscovery.getAllServices();
            for (ServiceInstance<Map<String, String>> instance : instances) {
                ServiceInfo info = new ServiceInfo();
                info.setId(instance.getId());
                info.setName(instance.getName());
                info.setAddress(instance.getAddress());
                info.setPort(instance.getPort());
                info.setMetadata(instance.getPayload());
                services.add(info);
            }
        } catch (Exception e) {
            LOGGER.error("获取服务列表失败", e);
        }
        return services;
    }

    /**
     * 检查 ZooKeeper 连接状态
     */
    public boolean isZookeeperConnected() {
        return serviceDiscovery.isConnected();
    }

    // ========== 在线客服系统监控方法 ==========

    /**
     * 获取客服系统统计
     */
    public CustomerServiceStats getCustomerServiceStats() {
        CustomerServiceStats stats = new CustomerServiceStats();

        // 会话统计
        CustomerServiceStats.SessionStats sessionStats = new CustomerServiceStats.SessionStats();
        List<Session> allSessions = sessionStore.findAll();
        sessionStats.setTotal(allSessions.size());
        sessionStats.setWaiting(sessionStore.findByStatus(SessionStatus.WAITING).size());
        sessionStats.setActive(sessionStore.findByStatus(SessionStatus.ACTIVE).size());
        sessionStats.setClosed(sessionStore.findByStatus(SessionStatus.CLOSED).size());
        stats.setSession(sessionStats);

        // 坐席统计
        CustomerServiceStats.AgentStats agentStats = new CustomerServiceStats.AgentStats();
        List<Agent> allAgents = new ArrayList<>();
        agentRegistry.findAll().forEach(allAgents::add);

        agentStats.setTotal(allAgents.size());
        agentStats.setOnline((int) allAgents.stream().filter(a -> a.getStatus() == AgentStatus.ONLINE).count());
        agentStats.setBusy((int) allAgents.stream().filter(a -> a.getStatus() == AgentStatus.BUSY).count());
        agentStats.setOffline((int) allAgents.stream().filter(a -> a.getStatus() == AgentStatus.OFFLINE).count());

        // 坐席列表
        List<AgentInfo> agentInfos = allAgents.stream().map(agent -> {
            AgentInfo info = new AgentInfo();
            info.setAgentId(agent.getAgentId());
            info.setAgentName(agent.getAgentName());
            info.setStatus(agent.getStatus().name());
            info.setCurrentSessions(agent.getCurrentSessions());
            info.setMaxSessions(agent.getMaxSessions());
            info.setBackendId(agent.getBackendId());
            return info;
        }).collect(Collectors.toList());
        agentStats.setAgents(agentInfos);
        stats.setAgent(agentStats);

        return stats;
    }

    /**
     * 获取活跃会话列表
     */
    public List<SessionInfo> getActiveSessions() {
        List<Session> sessions = new ArrayList<>();
        sessions.addAll(sessionStore.findByStatus(SessionStatus.WAITING));
        sessions.addAll(sessionStore.findByStatus(SessionStatus.ACTIVE));

        return sessions.stream().map(session -> {
            SessionInfo info = new SessionInfo();
            info.setSessionId(session.getSessionId());
            info.setCustomerId(session.getCustomerId());
            info.setCustomerName(session.getCustomerName());
            info.setAgentId(session.getAgentId());
            info.setStatus(session.getStatus().name());
            info.setBackendId(session.getBackendId());
            info.setCreateTime(session.getCreateTime());
            info.setUpdateTime(session.getUpdateTime());
            return info;
        }).collect(Collectors.toList());
    }

    /**
     * 获取坐席列表
     */
    public List<AgentInfo> getAgents() {
        List<Agent> allAgents = new ArrayList<>();
        agentRegistry.findAll().forEach(allAgents::add);

        return allAgents.stream().map(agent -> {
            AgentInfo info = new AgentInfo();
            info.setAgentId(agent.getAgentId());
            info.setAgentName(agent.getAgentName());
            info.setStatus(agent.getStatus().name());
            info.setCurrentSessions(agent.getCurrentSessions());
            info.setMaxSessions(agent.getMaxSessions());
            info.setBackendId(agent.getBackendId());
            return info;
        }).collect(Collectors.toList());
    }

    /**
     * 查询会话列表（支持分页和条件筛选）
     */
    public PageResponse<SessionInfo> querySessions(SessionQueryRequest request) {
        List<Session> allSessions = sessionStore.query(
                request.getSessionId(),
                request.getCustomerId(),
                request.getAgentId(),
                request.getStatus(),
                request.getStartTime(),
                request.getEndTime()
        );

        long total = allSessions.size();
        int page = request.getPage();
        int size = request.getSize();

        // 分页
        List<SessionInfo> content = allSessions.stream()
                .skip((long) page * size)
                .limit(size)
                .map(session -> {
                    SessionInfo info = new SessionInfo();
                    info.setSessionId(session.getSessionId());
                    info.setCustomerId(session.getCustomerId());
                    info.setCustomerName(session.getCustomerName());
                    info.setAgentId(session.getAgentId());
                    info.setStatus(session.getStatus().name());
                    info.setBackendId(session.getBackendId());
                    info.setCreateTime(session.getCreateTime());
                    info.setUpdateTime(session.getUpdateTime());
                    return info;
                })
                .collect(Collectors.toList());

        return new PageResponse<>(content, total, page, size);
    }

    /**
     * 根据会话ID获取会话详情
     */
    public SessionInfo getSessionDetail(String sessionId) {
        return sessionStore.findById(sessionId)
                .map(session -> {
                    SessionInfo info = new SessionInfo();
                    info.setSessionId(session.getSessionId());
                    info.setCustomerId(session.getCustomerId());
                    info.setCustomerName(session.getCustomerName());
                    info.setAgentId(session.getAgentId());
                    info.setStatus(session.getStatus().name());
                    info.setBackendId(session.getBackendId());
                    info.setCreateTime(session.getCreateTime());
                    info.setUpdateTime(session.getUpdateTime());
                    return info;
                })
                .orElse(null);
    }

    /**
     * 获取最近N条会话
     */
    public List<SessionInfo> getRecentSessions(int limit) {
        return sessionStore.findAll().stream()
                .sorted((a, b) -> Long.compare(b.getCreateTime(), a.getCreateTime()))
                .limit(limit)
                .map(session -> {
                    SessionInfo info = new SessionInfo();
                    info.setSessionId(session.getSessionId());
                    info.setCustomerId(session.getCustomerId());
                    info.setCustomerName(session.getCustomerName());
                    info.setAgentId(session.getAgentId());
                    info.setStatus(session.getStatus().name());
                    info.setBackendId(session.getBackendId());
                    info.setCreateTime(session.getCreateTime());
                    info.setUpdateTime(session.getUpdateTime());
                    return info;
                })
                .collect(Collectors.toList());
    }

    /**
     * 获取当前节点（Router）的指标
     */
    public NodeMetrics getRouterMetrics() {
        NodeMetrics metrics = new NodeMetrics();
        metrics.setNodeId("customer-frontend");
        metrics.setNodeType("ROUTER");
        metrics.setStatus(nettyServer.isRunning() ? "RUNNING" : "STOPPED");

        // 获取本地地址
        try {
            metrics.setAddress(java.net.InetAddress.getLocalHost().getHostAddress());
        } catch (Exception e) {
            metrics.setAddress("localhost");
        }
        metrics.setPort(nettyServer.getPort());

        // 运行时间
        long startTime = nettyServer.getStartTime();
        if (startTime > 0) {
            metrics.setUptimeMillis(System.currentTimeMillis() - startTime);
            metrics.setStartTime(new SimpleDateFormat("yyyy-MM-dd HH:mm:ss").format(new Date(startTime)));
        }

        // JVM 指标
        OperatingSystemMXBean osBean = ManagementFactory.getOperatingSystemMXBean();
        MemoryMXBean memoryBean = ManagementFactory.getMemoryMXBean();
        ThreadMXBean threadBean = ManagementFactory.getThreadMXBean();

        // CPU 使用率
        if (osBean instanceof com.sun.management.OperatingSystemMXBean sunOsBean) {
            metrics.setCpuUsage(Math.round(sunOsBean.getProcessCpuLoad() * 10000.0) / 100.0);
        }

        // 内存使用
        long usedMemory = memoryBean.getHeapMemoryUsage().getUsed();
        long maxMemory = memoryBean.getHeapMemoryUsage().getMax();
        metrics.setMemoryUsed(usedMemory);
        metrics.setMemoryMax(maxMemory);
        if (maxMemory > 0) {
            metrics.setMemoryUsagePercent(Math.round(usedMemory * 10000.0 / maxMemory) / 100.0);
        }

        // 线程数
        metrics.setThreadCount(threadBean.getThreadCount());
        metrics.setPeakThreadCount(threadBean.getPeakThreadCount());

        // 连接数
        metrics.setActiveConnections(connectionManager.getConnectionCount());
        metrics.setTotalConnections(connectionManager.getConnectionCount());

        return metrics;
    }

    /**
     * 获取 ZooKeeper 元数据
     */
    public ZookeeperMetadata getZookeeperMetadata() {
        ZookeeperMetadata metadata = new ZookeeperMetadata();
        metadata.setConnected(serviceDiscovery.isConnected());

        try {
            metadata.setConnectString(serviceDiscovery.getConnectString());
        } catch (Exception e) {
            metadata.setConnectString("N/A");
        }

        List<ZookeeperMetadata.ServiceMetadata> services = new ArrayList<>();
        try {
            Collection<ServiceInstance<Map<String, String>>> instances = serviceDiscovery.getAllServices();
            for (ServiceInstance<Map<String, String>> instance : instances) {
                ZookeeperMetadata.ServiceMetadata sm = new ZookeeperMetadata.ServiceMetadata();
                sm.setServiceId(instance.getId());
                sm.setServiceName(instance.getName());
                sm.setAddress(instance.getAddress());
                sm.setPort(instance.getPort());
                sm.setMetadata(instance.getPayload());
                services.add(sm);
            }
        } catch (Exception e) {
            LOGGER.error("获取ZooKeeper服务列表失败", e);
        }
        metadata.setServices(services);

        return metadata;
    }

    /**
     * 获取客户 WebSocket 连接数
     */
    public int getCustomerWebSocketCount() {
        return customerWebSocketHandler.getConnectionCount();
    }

    /**
     * 获取当前节点的 Netty 端口
     */
    public int getNettyPort() {
        return nettyServer.getPort();
    }

    /**
     * 获取当前前端节点信息
     */
    public FrontendNodeInfo getFrontendNodeInfo() {
        FrontendNodeInfo info = new FrontendNodeInfo();
        info.setNodeId(serviceId);
        info.setNodeType("FRONTEND");
        info.setHttpPort(httpPort);
        info.setNettyPort(nettyServer.getPort());
        info.setStatus(nettyServer.isRunning() ? "RUNNING" : "STOPPED");
        info.setNettyConnections(connectionManager.getConnectionCount());
        info.setWebSocketConnections(customerWebSocketHandler.getConnectionCount());

        // 运行时间
        long startTime = nettyServer.getStartTime();
        if (startTime > 0) {
            info.setUptimeMillis(System.currentTimeMillis() - startTime);
            info.setStartTime(new SimpleDateFormat("yyyy-MM-dd HH:mm:ss").format(new Date(startTime)));
        }

        // JVM 指标
        OperatingSystemMXBean osBean = ManagementFactory.getOperatingSystemMXBean();
        MemoryMXBean memoryBean = ManagementFactory.getMemoryMXBean();
        ThreadMXBean threadBean = ManagementFactory.getThreadMXBean();

        if (osBean instanceof com.sun.management.OperatingSystemMXBean sunOsBean) {
            info.setCpuUsage(Math.round(sunOsBean.getProcessCpuLoad() * 10000.0) / 100.0);
        }

        long usedMemory = memoryBean.getHeapMemoryUsage().getUsed();
        long maxMemory = memoryBean.getHeapMemoryUsage().getMax();
        info.setMemoryUsed(usedMemory);
        info.setMemoryMax(maxMemory);
        if (maxMemory > 0) {
            info.setMemoryUsagePercent(Math.round(usedMemory * 10000.0 / maxMemory) / 100.0);
        }

        info.setThreadCount(threadBean.getThreadCount());
        info.setPeakThreadCount(threadBean.getPeakThreadCount());

        try {
            info.setAddress(java.net.InetAddress.getLocalHost().getHostAddress());
        } catch (Exception e) {
            info.setAddress("localhost");
        }

        return info;
    }

    /**
     * 获取所有前端节点信息
     */
    public List<FrontendNodeInfo> getAllFrontendNodes() {
        List<FrontendNodeInfo> nodes = new ArrayList<>();

        // 添加当前节点
        nodes.add(getFrontendNodeInfo());

        // 从 ZooKeeper 获取其他前端节点信息
        try {
            Collection<ServiceInstance<Map<String, String>>> instances = serviceDiscovery.getAllServices();
            for (ServiceInstance<Map<String, String>> instance : instances) {
                Map<String, String> metadata = instance.getPayload();
                if (metadata != null && "router".equals(metadata.get("service-type"))) {
                    // 跳过当前节点（已添加）
                    if (instance.getId().equals(serviceId)) {
                        continue;
                    }

                    // 从元数据获取 HTTP 端口
                    String httpPortStr = metadata.get("http-port");
                    int httpPort = 8080;
                    if (httpPortStr != null) {
                        try {
                            httpPort = Integer.parseInt(httpPortStr);
                        } catch (NumberFormatException e) {
                            httpPort = 8080;
                        }
                    }

                    // 尝试从远程节点获取实时数据
                    FrontendNodeInfo node = fetchRemoteFrontendNodeInfo(instance.getAddress(), httpPort, instance.getId());
                    if (node != null) {
                        nodes.add(node);
                    } else {
                        // 如果无法获取实时数据，使用 ZooKeeper 数据
                        node = new FrontendNodeInfo();
                        node.setNodeId(instance.getId());
                        node.setNodeType("FRONTEND");
                        node.setAddress(instance.getAddress());
                        node.setHttpPort(httpPort);
                        node.setNettyPort(instance.getPort());
                        node.setStatus("ONLINE");
                        node.setNettyConnections(0);
                        node.setWebSocketConnections(0);

                        // 设置启动时间
                        String startupTimeStr = metadata.get("startup-time");
                        if (startupTimeStr != null) {
                            try {
                                long startupTime = Long.parseLong(startupTimeStr);
                                node.setStartTime(new SimpleDateFormat("yyyy-MM-dd HH:mm:ss").format(new Date(startupTime)));
                                node.setUptimeMillis(System.currentTimeMillis() - startupTime);
                            } catch (NumberFormatException e) {
                                // ignore
                            }
                        }
                        nodes.add(node);
                    }
                }
            }
        } catch (Exception e) {
            LOGGER.error("获取前端节点列表失败", e);
        }

        return nodes;
    }

    /**
     * 从远程前端节点获取实时信息
     */
    private FrontendNodeInfo fetchRemoteFrontendNodeInfo(String address, int httpPort, String nodeId) {
        try {
            HttpClient client = HttpClient.newBuilder()
                    .connectTimeout(Duration.ofSeconds(2))
                    .build();

            String url = String.format("http://%s:%d/api/monitor/health", address, httpPort);
            HttpRequest request = HttpRequest.newBuilder()
                    .uri(URI.create(url))
                    .timeout(Duration.ofSeconds(3))
                    .GET()
                    .build();

            HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());

            if (response.statusCode() == 200) {
                ObjectMapper mapper = new ObjectMapper();
                @SuppressWarnings("unchecked")
                Map<String, Object> healthData = mapper.readValue(response.body(), Map.class);

                FrontendNodeInfo node = new FrontendNodeInfo();
                node.setNodeId(nodeId);
                node.setNodeType("FRONTEND");
                node.setAddress(address);
                node.setHttpPort(httpPort);
                node.setStatus("RUNNING");

                // 解析健康数据
                Object nettyConn = healthData.get("nettyConnections");
                if (nettyConn instanceof Number) {
                    node.setNettyConnections(((Number) nettyConn).intValue());
                }

                Object wsConn = healthData.get("webSocketConnections");
                if (wsConn instanceof Number) {
                    node.setWebSocketConnections(((Number) wsConn).intValue());
                }

                Object nettyPort = healthData.get("nettyPort");
                if (nettyPort instanceof Number) {
                    node.setNettyPort(((Number) nettyPort).intValue());
                }

                // 解析启动时间和运行时长
                Object startTime = healthData.get("startTime");
                if (startTime instanceof String) {
                    node.setStartTime((String) startTime);
                }

                Object uptimeMillis = healthData.get("uptimeMillis");
                if (uptimeMillis instanceof Number) {
                    node.setUptimeMillis(((Number) uptimeMillis).longValue());
                }

                return node;
            }
        } catch (Exception e) {
            LOGGER.debug("无法获取远程前端节点 {}:{} 的健康数据: {}", address, httpPort, e.getMessage());
        }
        return null;
    }

    /**
     * 获取所有坐席后台节点信息
     */
    public List<BackendNodeInfo> getAllBackendNodes() {
        List<BackendNodeInfo> nodes = new ArrayList<>();

        // 从连接中获取 Backend 节点信息
        List<ConnectionStatus> connections = connectionManager.getConnectionDetails();
        Map<String, List<ConnectionStatus>> backendConnections = new HashMap<>();

        for (ConnectionStatus conn : connections) {
            if (conn.getServiceId() != null && conn.getServiceId().contains("backend")) {
                backendConnections.computeIfAbsent(conn.getServiceId(), k -> new ArrayList<>()).add(conn);
            }
        }

        for (Map.Entry<String, List<ConnectionStatus>> entry : backendConnections.entrySet()) {
            BackendNodeInfo node = new BackendNodeInfo();
            node.setNodeId(entry.getKey());
            node.setNodeType("BACKEND");
            node.setNettyConnectionCount(1); // 每个 backend 建立一个 Netty 连接

            // 计算该 backend 上的坐席数量
            int agentCount = 0;
            for (Agent a : agentRegistry.findAll()) {
                if (entry.getKey().equals(a.getBackendId())) {
                    agentCount++;
                }
            }
            node.setAgentCount(agentCount);

            // 获取连接状态
            List<ConnectionStatus> conns = entry.getValue();
            if (!conns.isEmpty()) {
                node.setStatus(conns.get(0).getStatus());
                node.setAddress(conns.get(0).getRemoteAddress() != null ?
                    conns.get(0).getRemoteAddress().replace("/", "").split(":")[0] : "N/A");
            }

            nodes.add(node);
        }

        return nodes;
    }

    /**
     * 获取所有节点指标（包括Frontend和Backend）
     */
    public List<NodeMetrics> getAllNodeMetrics() {
        List<NodeMetrics> allMetrics = new ArrayList<>();

        // 添加 Frontend 节点指标
        allMetrics.add(getRouterMetrics());

        // 从连接中获取 Backend 节点
        List<ConnectionStatus> connections = connectionManager.getConnectionDetails();
        for (ConnectionStatus conn : connections) {
            if (conn.getServiceId() != null && conn.getServiceId().contains("backend")) {
                NodeMetrics backendMetrics = new NodeMetrics();
                backendMetrics.setNodeId(conn.getServiceId());
                backendMetrics.setNodeType("BACKEND");
                backendMetrics.setAddress(conn.getRemoteAddress() != null ?
                    conn.getRemoteAddress().replace("/", "").split(":")[0] : "N/A");
                backendMetrics.setStatus(conn.getStatus());
                backendMetrics.setActiveConnections(1);
                allMetrics.add(backendMetrics);
            }
        }

        return allMetrics;
    }

    /**
     * 前端节点信息 DTO
     */
    public static class FrontendNodeInfo {
        private String nodeId;
        private String nodeType;
        private String address;
        private int httpPort;
        private int nettyPort;
        private String status;
        private int nettyConnections;
        private int webSocketConnections;
        private String startTime;
        private long uptimeMillis;
        private double cpuUsage;
        private long memoryUsed;
        private long memoryMax;
        private double memoryUsagePercent;
        private int threadCount;
        private int peakThreadCount;

        // Getters and Setters
        public String getNodeId() { return nodeId; }
        public void setNodeId(String nodeId) { this.nodeId = nodeId; }
        public String getNodeType() { return nodeType; }
        public void setNodeType(String nodeType) { this.nodeType = nodeType; }
        public String getAddress() { return address; }
        public void setAddress(String address) { this.address = address; }
        public int getHttpPort() { return httpPort; }
        public void setHttpPort(int httpPort) { this.httpPort = httpPort; }
        public int getNettyPort() { return nettyPort; }
        public void setNettyPort(int nettyPort) { this.nettyPort = nettyPort; }
        public String getStatus() { return status; }
        public void setStatus(String status) { this.status = status; }
        public int getNettyConnections() { return nettyConnections; }
        public void setNettyConnections(int nettyConnections) { this.nettyConnections = nettyConnections; }
        public int getWebSocketConnections() { return webSocketConnections; }
        public void setWebSocketConnections(int webSocketConnections) { this.webSocketConnections = webSocketConnections; }
        public String getStartTime() { return startTime; }
        public void setStartTime(String startTime) { this.startTime = startTime; }
        public long getUptimeMillis() { return uptimeMillis; }
        public void setUptimeMillis(long uptimeMillis) { this.uptimeMillis = uptimeMillis; }
        public double getCpuUsage() { return cpuUsage; }
        public void setCpuUsage(double cpuUsage) { this.cpuUsage = cpuUsage; }
        public long getMemoryUsed() { return memoryUsed; }
        public void setMemoryUsed(long memoryUsed) { this.memoryUsed = memoryUsed; }
        public long getMemoryMax() { return memoryMax; }
        public void setMemoryMax(long memoryMax) { this.memoryMax = memoryMax; }
        public double getMemoryUsagePercent() { return memoryUsagePercent; }
        public void setMemoryUsagePercent(double memoryUsagePercent) { this.memoryUsagePercent = memoryUsagePercent; }
        public int getThreadCount() { return threadCount; }
        public void setThreadCount(int threadCount) { this.threadCount = threadCount; }
        public int getPeakThreadCount() { return peakThreadCount; }
        public void setPeakThreadCount(int peakThreadCount) { this.peakThreadCount = peakThreadCount; }
    }

    /**
     * 坐席后台节点信息 DTO
     */
    public static class BackendNodeInfo {
        private String nodeId;
        private String nodeType;
        private String address;
        private String status;
        private int nettyConnectionCount;
        private int agentCount;

        // Getters and Setters
        public String getNodeId() { return nodeId; }
        public void setNodeId(String nodeId) { this.nodeId = nodeId; }
        public String getNodeType() { return nodeType; }
        public void setNodeType(String nodeType) { this.nodeType = nodeType; }
        public String getAddress() { return address; }
        public void setAddress(String address) { this.address = address; }
        public String getStatus() { return status; }
        public void setStatus(String status) { this.status = status; }
        public int getNettyConnectionCount() { return nettyConnectionCount; }
        public void setNettyConnectionCount(int nettyConnectionCount) { this.nettyConnectionCount = nettyConnectionCount; }
        public int getAgentCount() { return agentCount; }
        public void setAgentCount(int agentCount) { this.agentCount = agentCount; }
    }
}
