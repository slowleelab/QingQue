package com.lumio.mcp;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.ai.tool.ToolCallbackProvider;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 上下文加载与工具注册校验：Spring AI MCP Server 自动装配可用，
 * 且恰好暴露 22 个信用卡工具（自动收集所有 CreditCardTool 服务）。
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class McpServerApplicationTests {

    @Autowired
    private ToolCallbackProvider toolCallbackProvider;

    @Test
    void contextLoadsAndExposesAllTools() {
        assertThat(toolCallbackProvider).isNotNull();
        assertThat(toolCallbackProvider.getToolCallbacks()).hasSize(22);
    }
}
