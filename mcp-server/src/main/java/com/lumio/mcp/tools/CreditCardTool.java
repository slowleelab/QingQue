package com.lumio.mcp.tools;

/**
 * 信用卡 MCP 工具标记接口。
 *
 * <p>所有承载 {@code @Tool} 方法的工具类实现本接口，使
 * {@link com.lumio.mcp.config.ToolConfiguration} 能通过 {@code List<CreditCardTool>} 自动收集全部工具 Bean
 * 并注册为 {@code ToolCallbackProvider}——新增工具类只要实现本接口即自动生效，无需再改注册配置。</p>
 */
public interface CreditCardTool {
}
