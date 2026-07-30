package com.smartcs.mcp;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

/**
 * SmartCS 银行信用卡智能客服 MCP 工具服务入口。
 *
 * <p>本服务通过 Spring AI MCP Server（WebMVC 传输）对外暴露一组信用卡业务工具，
 * 供上游编排大脑（Python Bot/Assist 服务，经 Higress AI 网关）以 MCP 协议调用。</p>
 *
 * <p>架构：六边形（端口—适配器）。领域服务（{@code domain.service}）依赖端口
 * （{@code domain.port}），当前由内存 Mock 适配器（{@code adapter.mock}）实现，
 * 未来可无侵入替换为对接真实核心系统的适配器。</p>
 *
 * <p>安全红线：本工程为演示/参考实现，所有数据均来自内存 Mock 仓库，
 * <b>不连接任何真实银行核心系统</b>；卡号为构造的假卡号（非真实 PAN），仅用于演示，
 * 不存储 CVV、有效期等敏感要素，日志中仅记录卡号尾号。敏感（写类）工具的确认与授权治理
 * 由上游 Python 编排层（确认状态机 + ToolGuard）与网关负责。</p>
 */
@SpringBootApplication
@ConfigurationPropertiesScan
public class McpServerApplication {

    public static void main(String[] args) {
        SpringApplication.run(McpServerApplication.class, args);
    }
}
