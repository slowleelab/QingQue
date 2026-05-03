package com.example.customerserver.agent;

import com.example.common.model.Agent;
import com.example.common.model.AgentStatus;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.stream.Collectors;

/**
 * 坐席注册表
 * 管理所有注册的坐席信息
 */
@Component
public class AgentRegistry {
    private static final Logger LOGGER = LoggerFactory.getLogger(AgentRegistry.class);

    // 坐席ID -> 坐席信息
    private final Map<String, Agent> agents = new ConcurrentHashMap<>();
    // 后台节点ID -> 坐席ID列表
    private final Map<String, List<String>> backendAgents = new ConcurrentHashMap<>();

    /**
     * 注册坐席
     */
    public void registerAgent(Agent agent) {
        agents.put(agent.getAgentId(), agent);

        // 更新后台节点坐席列表
        String backendId = agent.getBackendId();
        if (backendId != null) {
            backendAgents.compute(backendId, (k, v) -> {
                if (v == null) {
                    v = new java.util.ArrayList<>();
                }
                if (!v.contains(agent.getAgentId())) {
                    v.add(agent.getAgentId());
                }
                return v;
            });
        }

        LOGGER.info("坐席注册成功: {} (后台: {}, 状态: {})",
                agent.getAgentId(), agent.getBackendId(), agent.getStatus());
    }

    /**
     * 注销坐席
     */
    public void unregisterAgent(String agentId) {
        Agent agent = agents.remove(agentId);
        if (agent != null) {
            String backendId = agent.getBackendId();
            if (backendId != null) {
                backendAgents.computeIfPresent(backendId, (k, v) -> {
                    v.remove(agentId);
                    return v.isEmpty() ? null : v;
                });
            }
            LOGGER.info("坐席注销: {}", agentId);
        }
    }

    /**
     * 更新坐席状态
     */
    public void updateAgentStatus(String agentId, AgentStatus status) {
        Agent agent = agents.get(agentId);
        if (agent != null) {
            agent.setStatus(status);
            LOGGER.info("坐席 {} 状态更新为: {}", agentId, status);
        }
    }

    /**
     * 根据ID查找坐席
     */
    public Optional<Agent> findById(String agentId) {
        return Optional.ofNullable(agents.get(agentId));
    }

    /**
     * 获取所有可用坐席（在线且有可用容量）
     */
    public List<Agent> getAvailableAgents() {
        return agents.values().stream()
                .filter(Agent::canAcceptSession)
                .collect(Collectors.toList());
    }

    /**
     * 获取所有在线坐席
     */
    public List<Agent> getOnlineAgents() {
        return agents.values().stream()
                .filter(a -> a.getStatus() == AgentStatus.ONLINE || a.getStatus() == AgentStatus.BUSY)
                .collect(Collectors.toList());
    }

    /**
     * 获取后台节点的所有坐席
     */
    public List<Agent> getAgentsByBackendId(String backendId) {
        List<String> agentIds = backendAgents.get(backendId);
        if (agentIds == null || agentIds.isEmpty()) {
            return List.of();
        }
        return agentIds.stream()
                .map(agents::get)
                .filter(java.util.Objects::nonNull)
                .collect(Collectors.toList());
    }

    /**
     * 注销后台节点的所有坐席
     */
    public void unregisterBackendAgents(String backendId) {
        List<String> agentIds = backendAgents.remove(backendId);
        if (agentIds != null) {
            for (String agentId : agentIds) {
                agents.remove(agentId);
                LOGGER.info("坐席已注销（后台节点离线）: {}", agentId);
            }
        }
    }

    /**
     * 获取坐席总数
     */
    public int getAgentCount() {
        return agents.size();
    }

    /**
     * 获取在线坐席数
     */
    public int getOnlineAgentCount() {
        return (int) agents.values().stream()
                .filter(a -> a.getStatus() == AgentStatus.ONLINE || a.getStatus() == AgentStatus.BUSY)
                .count();
    }

    /**
     * 获取所有坐席
     */
    public Iterable<Agent> findAll() {
        return agents.values();
    }
}
