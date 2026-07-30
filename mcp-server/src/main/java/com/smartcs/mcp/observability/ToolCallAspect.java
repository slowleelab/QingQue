package com.smartcs.mcp.observability;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import java.util.UUID;
import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.aspectj.lang.reflect.MethodSignature;
import org.slf4j.MDC;
import org.springframework.ai.tool.annotation.Tool;
import org.springframework.stereotype.Component;

/**
 * 工具调用可观测切面：为每个 {@code @Tool} 方法记录调用计数、耗时与结果（成功/失败），
 * 并在调用期间向 MDC 注入工具名与调用 ID，便于日志关联排障。
 *
 * <p>指标：{@code mcp_tool_calls_total{tool, outcome}}（计数）与
 * {@code mcp_tool_call_duration{tool, outcome}}（计时），经 actuator/prometheus 暴露。</p>
 */
@Aspect
@Component
public class ToolCallAspect {

    private static final String MDC_TOOL = "mcpTool";
    private static final String MDC_CALL_ID = "mcpCallId";

    private final MeterRegistry meterRegistry;

    public ToolCallAspect(MeterRegistry meterRegistry) {
        this.meterRegistry = meterRegistry;
    }

    @Around("@annotation(org.springframework.ai.tool.annotation.Tool)")
    public Object aroundToolCall(ProceedingJoinPoint pjp) throws Throwable {
        String toolName = resolveToolName(pjp);
        String callId = UUID.randomUUID().toString();
        MDC.put(MDC_TOOL, toolName);
        MDC.put(MDC_CALL_ID, callId);
        Timer.Sample sample = Timer.start(meterRegistry);
        String outcome = "success";
        try {
            return pjp.proceed();
        } catch (Throwable t) {
            outcome = "error";
            throw t;
        } finally {
            sample.stop(meterRegistry.timer("mcp_tool_call_duration", "tool", toolName, "outcome", outcome));
            meterRegistry.counter("mcp_tool_calls_total", "tool", toolName, "outcome", outcome).increment();
            MDC.remove(MDC_TOOL);
            MDC.remove(MDC_CALL_ID);
        }
    }

    private static String resolveToolName(ProceedingJoinPoint pjp) {
        MethodSignature signature = (MethodSignature) pjp.getSignature();
        Tool tool = signature.getMethod().getAnnotation(Tool.class);
        if (tool != null && tool.name() != null && !tool.name().isBlank()) {
            return tool.name();
        }
        return signature.getName();
    }
}
