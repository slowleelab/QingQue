package com.lumio.mcp.observability;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import io.micrometer.observation.ObservationRegistry;
import java.lang.reflect.Method;
import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.reflect.MethodSignature;
import org.junit.jupiter.api.Test;
import org.springframework.ai.tool.annotation.Tool;

/**
 * 工具调用切面指标测试：用 {@link SimpleMeterRegistry} 断言计数/计时按 tool、outcome 维度发射，
 * 且异常路径记为 error 并原样抛出。不依赖 Spring 容器与真实工具。
 *
 * <p>commit 3 新增: 切面用 {@link ObservationRegistry} 包装工具调用产 span,
 * 此处使用 {@link ObservationRegistry#NOOP} 验证 Observation 装配路径通, 指标
 * 仍按 tool/outcome 发射 (双轨: Observation 产 span, MeterRegistry 产 metric).</p>
 */
class ToolCallAspectTest {

    /** 提供一个带 {@code @Tool} 注解的方法供切面反射解析工具名。 */
    static class Sample {
        @Tool(name = "query_demo", description = "demo")
        public String queryDemo() {
            return "ok";
        }
    }

    private MethodSignature signatureForQueryDemo() throws Exception {
        Method method = Sample.class.getMethod("queryDemo");
        MethodSignature signature = mock(MethodSignature.class);
        when(signature.getMethod()).thenReturn(method);
        when(signature.getName()).thenReturn("queryDemo");
        return signature;
    }

    @Test
    void emitsSuccessMetrics() throws Throwable {
        SimpleMeterRegistry registry = new SimpleMeterRegistry();
        ToolCallAspect aspect = new ToolCallAspect(registry, ObservationRegistry.NOOP);

        MethodSignature signature = signatureForQueryDemo();
        ProceedingJoinPoint pjp = mock(ProceedingJoinPoint.class);
        when(pjp.getSignature()).thenReturn(signature);
        when(pjp.proceed()).thenReturn("ok");

        Object result = aspect.aroundToolCall(pjp);

        assertThat(result).isEqualTo("ok");
        assertThat(registry.get("mcp_tool_calls_total").tag("tool", "query_demo").tag("outcome", "success").counter().count())
                .isEqualTo(1.0);
        assertThat(registry.get("mcp_tool_call_duration").tag("tool", "query_demo").tag("outcome", "success").timer().count())
                .isEqualTo(1L);
    }

    @Test
    void emitsErrorMetricsAndRethrows() throws Throwable {
        SimpleMeterRegistry registry = new SimpleMeterRegistry();
        ToolCallAspect aspect = new ToolCallAspect(registry, ObservationRegistry.NOOP);

        MethodSignature signature = signatureForQueryDemo();
        ProceedingJoinPoint pjp = mock(ProceedingJoinPoint.class);
        when(pjp.getSignature()).thenReturn(signature);
        when(pjp.proceed()).thenThrow(new IllegalStateException("boom"));

        assertThatThrownBy(() -> aspect.aroundToolCall(pjp)).isInstanceOf(IllegalStateException.class);
        assertThat(registry.get("mcp_tool_calls_total").tag("tool", "query_demo").tag("outcome", "error").counter().count())
                .isEqualTo(1.0);
    }

    /**
     * commit 3: Observation 路径装配验证.
     * 使用 {@link ObservationRegistry#NOOP} (不会真产 span) 验证:
     * - 切面接受 ObservationRegistry 依赖 (双轨: span + metric)
     * - 切面跑通不抛 NoSuchBeanDefinitionException
     * - Counter/Timer 指标行为不变
     */
    @Test
    void observationPathIsWired() throws Throwable {
        SimpleMeterRegistry registry = new SimpleMeterRegistry();
        ToolCallAspect aspect = new ToolCallAspect(registry, ObservationRegistry.NOOP);

        MethodSignature signature = signatureForQueryDemo();
        ProceedingJoinPoint pjp = mock(ProceedingJoinPoint.class);
        when(pjp.getSignature()).thenReturn(signature);
        when(pjp.proceed()).thenReturn("ok");

        Object result = aspect.aroundToolCall(pjp);

        // 业务结果不变
        assertThat(result).isEqualTo("ok");
        // 双轨指标仍按 tool/outcome 发射
        assertThat(registry.get("mcp_tool_calls_total").tag("tool", "query_demo").tag("outcome", "success").counter().count())
                .isEqualTo(1.0);
        assertThat(registry.get("mcp_tool_call_duration").tag("tool", "query_demo").tag("outcome", "success").timer().count())
                .isEqualTo(1L);
    }

    /**
     * commit 3: 异常路径仍被 Observation 包装记录, 同时 Counter outcome=error 不变.
     * Noop Observation 不产 span 但 record_exception 调用本身不抛.
     */
    @Test
    void observationPathOnError() throws Throwable {
        SimpleMeterRegistry registry = new SimpleMeterRegistry();
        ToolCallAspect aspect = new ToolCallAspect(registry, ObservationRegistry.NOOP);

        MethodSignature signature = signatureForQueryDemo();
        ProceedingJoinPoint pjp = mock(ProceedingJoinPoint.class);
        when(pjp.getSignature()).thenReturn(signature);
        when(pjp.proceed()).thenThrow(new IllegalStateException("boom"));

        assertThatThrownBy(() -> aspect.aroundToolCall(pjp)).isInstanceOf(IllegalStateException.class);
        assertThat(registry.get("mcp_tool_calls_total").tag("tool", "query_demo").tag("outcome", "error").counter().count())
                .isEqualTo(1.0);
    }
}
