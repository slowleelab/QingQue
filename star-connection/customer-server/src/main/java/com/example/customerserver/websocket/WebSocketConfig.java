package com.example.customerserver.websocket;

import com.example.customerserver.config.WebSocketProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.config.annotation.EnableWebSocket;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;
import org.springframework.web.socket.server.standard.ServletServerContainerFactoryBean;

import java.time.Duration;

/**
 * 客户 WebSocket 配置
 */
@Configuration
@EnableWebSocket
@ConditionalOnProperty(prefix = "websocket.customer", name = "enabled", havingValue = "true", matchIfMissing = false)
public class WebSocketConfig implements WebSocketConfigurer {
    private static final Logger LOGGER = LoggerFactory.getLogger(WebSocketConfig.class);

    private final CustomerWebSocketHandler customerWebSocketHandler;
    private final WebSocketProperties webSocketProperties;

    @Autowired
    public WebSocketConfig(CustomerWebSocketHandler customerWebSocketHandler,
                           WebSocketProperties webSocketProperties) {
        this.customerWebSocketHandler = customerWebSocketHandler;
        this.webSocketProperties = webSocketProperties;
    }

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        String path = webSocketProperties.getCustomer().getPath();
        LOGGER.info("注册客户 WebSocket 处理器: {}/*", path);
        // 支持 /ws/customer/{sessionId} 格式的路径
        registry.addHandler(customerWebSocketHandler, path + "/*")
                .setAllowedOrigins("*");
    }

    @Bean
    public ServletServerContainerFactoryBean createWebSocketContainer() {
        ServletServerContainerFactoryBean container = new ServletServerContainerFactoryBean();
        container.setMaxTextMessageBufferSize(8192);
        container.setMaxBinaryMessageBufferSize(8192);
        container.setMaxSessionIdleTimeout(Duration.ofMinutes(30).toMillis());
        return container;
    }
}
