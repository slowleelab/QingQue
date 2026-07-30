package com.lumio.mcp.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * API-Key 鉴权过滤器（纵深防御）。
 *
 * <p>仅当 {@code lumio.security.api-key.enabled=true} 时注册生效——默认不注册,保证零回归。
 * 校验请求头是否携带白名单中的 API-Key;放行 actuator 健康探针;不匹配返回 401 JSON。</p>
 */
@Component
@ConditionalOnProperty(prefix = "lumio.security.api-key", name = "enabled", havingValue = "true")
public class ApiKeyAuthFilter extends OncePerRequestFilter {

    private final SecurityProperties properties;

    public ApiKeyAuthFilter(SecurityProperties properties) {
        this.properties = properties;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String path = request.getRequestURI();
        // 健康探针放行,便于容器/网关探活
        if (path != null && (path.startsWith("/actuator/health") || path.equals("/actuator"))) {
            chain.doFilter(request, response);
            return;
        }

        String presented = request.getHeader(properties.getHeader());
        List<String> allowed = properties.getKeys();
        boolean ok = StringUtils.hasText(presented) && allowed != null && allowed.contains(presented);
        if (!ok) {
            response.setStatus(HttpStatus.UNAUTHORIZED.value());
            response.setContentType(MediaType.APPLICATION_JSON_VALUE);
            response.setCharacterEncoding(StandardCharsets.UTF_8.name());
            response.getWriter().write("{\"error\":{\"code\":\"401\",\"message\":\"缺少或无效的 API Key\"}}");
            return;
        }
        chain.doFilter(request, response);
    }
}
