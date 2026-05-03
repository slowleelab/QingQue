package com.example.customerserver.agent;

import com.example.common.model.Agent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.util.Comparator;
import java.util.List;

/**
 * 坐席负载均衡器
 * 实现最少连接数优先策略
 */
@Component
public class AgentLoadBalancer {
    private static final Logger LOGGER = LoggerFactory.getLogger(AgentLoadBalancer.class);

    private final AgentRegistry agentRegistry;

    @Autowired
    public AgentLoadBalancer(AgentRegistry agentRegistry) {
        this.agentRegistry = agentRegistry;
    }

    /**
     * 选择一个可用坐席
     * 使用最少连接数优先策略
     */
    public Agent selectAgent() {
        List<Agent> availableAgents = agentRegistry.getAvailableAgents();

        if (availableAgents.isEmpty()) {
            LOGGER.debug("没有可用的坐席");
            return null;
        }

        // 按当前会话数升序排序，选择会话数最少的坐席
        Agent selected = availableAgents.stream()
                .min(Comparator.comparingInt(Agent::getCurrentSessions))
                .orElse(null);

        if (selected != null) {
            LOGGER.debug("选中坐席: {}, 当前会话数: {}/{}",
                    selected.getAgentId(), selected.getCurrentSessions(), selected.getMaxSessions());
        }

        return selected;
    }

    /**
     * 选择指定坐席
     * 检查指定坐席是否可用
     */
    public Agent selectAgent(String agentId) {
        return agentRegistry.findById(agentId)
                .filter(Agent::canAcceptSession)
                .orElse(null);
    }
}
