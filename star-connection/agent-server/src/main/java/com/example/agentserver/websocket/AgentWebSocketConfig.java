package com.example.agentserver.websocket;

import com.example.agentserver.config.WebSocketProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.config.annotation.EnableWebSocket;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;
import org.springframework.web.socket.server.standard.ServletServerContainerFactoryBean;

import java.time.Duration;

/**
 * 坐席 WebSocket 配置
 */
@Configuration
@EnableWebSocket
@ConditionalOnProperty(prefix = "websocket.agent", name = "enabled", havingValue = "true", matchIfMissing = false)
public class AgentWebSocketConfig implements WebSocketConfigurer {
    private static final Logger LOGGER = LoggerFactory.getLogger(AgentWebSocketConfig.class);

    private final AgentWebSocketHandler agentWebSocketHandler;
    private final WebSocketProperties webSocketProperties;

    /**
     * 允许的前端域名，多个域名用逗号分隔
     * 前后端分离部署时需要配置为前端的实际域名
     * 示例: https://agent.example.com,http://localhost:3000
     */
    @Value("${cors.allowed-origins:*}")
    private String allowedOrigins;

    @Autowired
    public AgentWebSocketConfig(AgentWebSocketHandler agentWebSocketHandler,
                                WebSocketProperties webSocketProperties) {
        this.agentWebSocketHandler = agentWebSocketHandler;
        this.webSocketProperties = webSocketProperties;
    }

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        String path = webSocketProperties.getAgent().getPath();
        String[] origins = allowedOrigins.split(",");

        LOGGER.info("注册坐席 WebSocket 处理器: {}/*, 允许的源: {}", path, allowedOrigins);

        // 支持 /ws/agent/{agentId} 格式的路径
        // 前后端分离部署时，需配置前端域名
        registry.addHandler(agentWebSocketHandler, path + "/*")
                .setAllowedOrigins(origins);
    }

    @Bean
    public ServletServerContainerFactoryBean createWebSocketContainer() {
        ServletServerContainerFactoryBean container = new ServletServerContainerFactoryBean();
        container.setMaxTextMessageBufferSize(8192);
        container.setMaxBinaryMessageBufferSize(8192);
        container.setMaxSessionIdleTimeout(Duration.ofMinutes(60).toMillis());
        return container;
    }
}
