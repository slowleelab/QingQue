package com.example.agentserver.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.ResourceHandlerRegistry;
import org.springframework.web.servlet.config.annotation.ViewControllerRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * Web MVC 配置
 * 用于配置静态资源、视图控制器和跨域请求
 */
@Configuration
public class WebMvcConfig implements WebMvcConfigurer {

    /**
     * 允许的前端域名，多个域名用逗号分隔
     * 前后端分离部署时需要配置为前端的实际域名
     * 示例: https://agent.example.com,http://localhost:3000
     */
    @Value("${cors.allowed-origins:*}")
    private String allowedOrigins;

    @Override
    public void addResourceHandlers(ResourceHandlerRegistry registry) {
        // 配置静态资源路径
        registry.addResourceHandler("/static/**")
                .addResourceLocations("classpath:/static/");
    }

    @Override
    public void addViewControllers(ViewControllerRegistry registry) {
        // 根路径重定向到 index.html
        registry.addRedirectViewController("/", "/index.html");
    }

    /**
     * 配置跨域请求 (CORS)
     * 前后端分离部署时，前端需要通过此配置访问后端 API
     */
    @Override
    public void addCorsMappings(CorsRegistry registry) {
        String[] origins = allowedOrigins.split(",");

        // 判断是否为通配符配置
        boolean isWildcard = origins.length == 1 && "*".equals(origins[0].trim());

        if (isWildcard) {
            // 使用 allowedOriginPatterns 支持通配符和 credentials
            registry.addMapping("/api/**")
                    .allowedOriginPatterns("*")
                    .allowedMethods("GET", "POST", "PUT", "DELETE", "OPTIONS")
                    .allowedHeaders("*")
                    .allowCredentials(true)
                    .maxAge(3600);
        } else {
            // 指定具体的域名
            registry.addMapping("/api/**")
                    .allowedOrigins(origins)
                    .allowedMethods("GET", "POST", "PUT", "DELETE", "OPTIONS")
                    .allowedHeaders("*")
                    .allowCredentials(true)
                    .maxAge(3600);
        }
    }
}
