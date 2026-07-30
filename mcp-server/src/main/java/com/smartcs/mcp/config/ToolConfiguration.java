package com.smartcs.mcp.config;

import com.smartcs.mcp.tools.CreditCardTool;
import java.util.List;
import org.springframework.ai.tool.ToolCallbackProvider;
import org.springframework.ai.tool.method.MethodToolCallbackProvider;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * MCP 工具注册：自动收集所有实现 {@link CreditCardTool} 标记接口的 {@code @Tool} 服务 Bean，
 * 汇总为一个 {@link ToolCallbackProvider}，由 Spring AI MCP Server 自动装配并对外暴露。
 *
 * <p>共 22 个工具：12 个只读查询（账单/明细/年费/额度/额度调整历史/积分/权益/交易/分期方案/在办分期/还款历史/卡状态）
 * + 10 个敏感写操作（临时提额/永久提额/账单分期/取消分期/还款/自动还款/积分兑换/挂失/开卡激活/交易争议）。</p>
 *
 * <p>新增工具只需让其服务类实现 {@link CreditCardTool}，即被自动注册，无需修改本类。</p>
 */
@Configuration
public class ToolConfiguration {

    @Bean
    public ToolCallbackProvider creditCardToolCallbackProvider(List<CreditCardTool> tools) {
        return MethodToolCallbackProvider.builder()
                .toolObjects(tools.toArray())
                .build();
    }
}
