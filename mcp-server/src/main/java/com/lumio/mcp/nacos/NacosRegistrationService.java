package com.lumio.mcp.nacos;

import com.alibaba.nacos.api.NacosFactory;
import com.alibaba.nacos.api.naming.NamingService;
import com.alibaba.nacos.api.naming.pojo.Instance;
import jakarta.annotation.PreDestroy;
import java.net.InetAddress;
import java.util.Map;
import java.util.Properties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.ApplicationListener;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

/**
 * 将本 MCP Server 注册到 Nacos，供 Higress AI 网关经服务发现代理。
 *
 * <p><b>仅在 {@code spring.profiles.active=nacos} 时启用</b>：默认不激活，
 * 因此默认构建 / 运行 / 测试完全不受影响（零回归）。注册失败仅告警、不阻断服务启动，
 * 因为 MCP Server 的首要职责是对外提供工具，注册到 Nacos 属辅助能力。</p>
 */
@Component
@Profile("nacos")
public class NacosRegistrationService implements ApplicationListener<ApplicationReadyEvent> {

    private static final Logger LOGGER = LoggerFactory.getLogger(NacosRegistrationService.class);

    @Value("${lumio.nacos.server-addr:127.0.0.1:8848}")
    private String serverAddr;

    @Value("${lumio.nacos.namespace:public}")
    private String namespace;

    @Value("${lumio.nacos.service-name:lumio-mcp-server}")
    private String serviceName;

    @Value("${lumio.nacos.group:DEFAULT_GROUP}")
    private String group;

    @Value("${lumio.nacos.instance-ip:}")
    private String instanceIp;

    @Value("${server.port:8090}")
    private int port;

    private NamingService namingService;
    private String registeredIp;

    @Override
    public void onApplicationEvent(ApplicationReadyEvent event) {
        try {
            Properties props = new Properties();
            props.setProperty("serverAddr", serverAddr);
            if (namespace != null && !namespace.isBlank() && !"public".equals(namespace)) {
                props.setProperty("namespace", namespace);
            }
            namingService = NacosFactory.createNamingService(props);
            registeredIp = (instanceIp == null || instanceIp.isBlank())
                    ? InetAddress.getLocalHost().getHostAddress()
                    : instanceIp;

            Instance instance = new Instance();
            instance.setIp(registeredIp);
            instance.setPort(port);
            instance.setHealthy(true);
            instance.setEnabled(true);
            // 便于 Higress 识别 MCP 后端的传输形态与端点
            instance.setMetadata(Map.of(
                    "mcp.transport", "sse",
                    "mcp.sse.path", "/sse",
                    "mcp.message.path", "/mcp/message",
                    "mcp.tools", "22"));

            namingService.registerInstance(serviceName, group, instance);
            LOGGER.info("已注册到 Nacos: service={}, group={}, addr={}:{}, nacos={}",
                    serviceName, group, registeredIp, port, serverAddr);
        } catch (Exception e) {
            LOGGER.warn("注册到 Nacos 失败（不影响 MCP Server 对外提供工具）: {}", e.getMessage());
        }
    }

    /**
     * 优雅停机时从 Nacos 注销本实例。
     */
    @PreDestroy
    public void deregister() {
        if (namingService == null || registeredIp == null) {
            return;
        }
        try {
            namingService.deregisterInstance(serviceName, group, registeredIp, port);
            LOGGER.info("已从 Nacos 注销: service={}, addr={}:{}", serviceName, registeredIp, port);
        } catch (Exception e) {
            LOGGER.warn("从 Nacos 注销失败: {}", e.getMessage());
        }
    }
}
