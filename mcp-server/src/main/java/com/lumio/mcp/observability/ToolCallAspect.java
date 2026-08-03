package com.lumio.mcp.observability;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import io.micrometer.observation.Observation;
import io.micrometer.observation.ObservationRegistry;
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
 *
 * <p>链路追踪：通过 {@link Observation} 包装，工具调用产 {@code mcp.tool.call} span，
 * 与上游 Python {@code MCP.call_tool} span 串成父子链。异常路径自动
 * {@code record_exception} + {@code set_status=ERROR}。现有 Counter/Timer 指标保留，
 * 双轨运行（Observation 产 span，MeterRegistry 产 metric）。</p>
 */
@Aspect
@Component
public class ToolCallAspect {

    private static final String MDC_TOOL = "mcpTool";
    private static final String MDC_CALL_ID = "mcpCallId";

    private final MeterRegistry meterRegistry;
    private final ObservationRegistry observationRegistry;

    public ToolCallAspect(MeterRegistry meterRegistry, ObservationRegistry observationRegistry) {
        this.meterRegistry = meterRegistry;
        this.observationRegistry = observationRegistry;
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
            // Observation 包装：与 Python MCP.call_tool span 串成父子链
            // 注意: outcome 在 finally 之前未定, 故用临时变量 + finally 内 set
            return Observation.createNotStarted("mcp.tool.call", observationRegistry)
                    .lowCardinalityKeyValue("tool", toolName)
                    .observe(() -> {
                        try {
                            return pjp.proceed();
                        } catch (Throwable t) {
                            // 把 outcome 暴露给外层 finally: 通过局部数组 hack
                            // 注: Observation 内部已 record_exception + set_status=ERROR
                            throw new AspectWrappedException(t);
                        }
                    });
        } catch (AspectWrappedException awe) {
            outcome = "error";
            throw awe.getCause();
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

    /**
     * 内部包装异常: 把原始 Throwable 透传给外层 catch, 让 Counter/Timer 仍按 outcome=error
     * 记录. Observation 内部已对 cause 做 record_exception + set_status.
     */
    private static final class AspectWrappedException extends RuntimeException {
        AspectWrappedException(Throwable cause) {
            super(cause);
        }
    }
}
