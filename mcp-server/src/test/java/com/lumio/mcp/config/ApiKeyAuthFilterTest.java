package com.lumio.mcp.config;

import static org.assertj.core.api.Assertions.assertThat;

import jakarta.servlet.ServletException;
import java.io.IOException;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

/**
 * API-Key 鉴权过滤器测试：放行健康探针、放行合法 Key、拦截缺失/错误 Key。
 */
class ApiKeyAuthFilterTest {

    private ApiKeyAuthFilter filter;

    @BeforeEach
    void setUp() {
        SecurityProperties props = new SecurityProperties();
        props.setEnabled(true);
        props.setHeader("X-MCP-Api-Key");
        props.setKeys(List.of("secret-key-1", "secret-key-2"));
        filter = new ApiKeyAuthFilter(props);
    }

    @Test
    void validKeyPassesThrough() throws ServletException, IOException {
        MockHttpServletRequest req = new MockHttpServletRequest("GET", "/mcp/message");
        req.addHeader("X-MCP-Api-Key", "secret-key-2");
        MockHttpServletResponse res = new MockHttpServletResponse();
        MockFilterChain chain = new MockFilterChain();

        filter.doFilter(req, res, chain);

        assertThat(res.getStatus()).isEqualTo(HttpStatus.OK.value());
        assertThat(chain.getRequest()).isNotNull();
    }

    @Test
    void missingKeyRejectedWith401() throws ServletException, IOException {
        MockHttpServletRequest req = new MockHttpServletRequest("GET", "/mcp/message");
        MockHttpServletResponse res = new MockHttpServletResponse();
        MockFilterChain chain = new MockFilterChain();

        filter.doFilter(req, res, chain);

        assertThat(res.getStatus()).isEqualTo(HttpStatus.UNAUTHORIZED.value());
        assertThat(res.getContentAsString()).contains("API Key");
        assertThat(chain.getRequest()).isNull();
    }

    @Test
    void wrongKeyRejectedWith401() throws ServletException, IOException {
        MockHttpServletRequest req = new MockHttpServletRequest("GET", "/mcp/message");
        req.addHeader("X-MCP-Api-Key", "nope");
        MockHttpServletResponse res = new MockHttpServletResponse();
        MockFilterChain chain = new MockFilterChain();

        filter.doFilter(req, res, chain);

        assertThat(res.getStatus()).isEqualTo(HttpStatus.UNAUTHORIZED.value());
    }

    @Test
    void healthProbeBypassesAuth() throws ServletException, IOException {
        MockHttpServletRequest req = new MockHttpServletRequest("GET", "/actuator/health");
        MockHttpServletResponse res = new MockHttpServletResponse();
        MockFilterChain chain = new MockFilterChain();

        filter.doFilter(req, res, chain);

        assertThat(res.getStatus()).isEqualTo(HttpStatus.OK.value());
        assertThat(chain.getRequest()).isNotNull();
    }
}
