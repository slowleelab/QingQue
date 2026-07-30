package com.smartcs.mcp.config;

import java.util.ArrayList;
import java.util.List;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 服务端 API-Key 鉴权配置（{@code smartcs.security.api-key.*}）。
 *
 * <p>纵深防御:即便前置有 Higress 网关做统一鉴权,服务端仍可开启独立的 API-Key 校验,
 * 避免绕过网关直连。<b>默认关闭（enabled=false），行为与现状完全一致（零回归）。</b></p>
 */
@ConfigurationProperties(prefix = "smartcs.security.api-key")
public class SecurityProperties {

    /** 是否启用 API-Key 校验。默认关闭。 */
    private boolean enabled = false;

    /** 携带 API-Key 的请求头名称。 */
    private String header = "X-MCP-Api-Key";

    /** 允许的 API-Key 白名单（任一匹配即放行）。 */
    private List<String> keys = new ArrayList<>();

    public boolean isEnabled() {
        return enabled;
    }

    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
    }

    public String getHeader() {
        return header;
    }

    public void setHeader(String header) {
        this.header = header;
    }

    public List<String> getKeys() {
        return keys;
    }

    public void setKeys(List<String> keys) {
        this.keys = keys;
    }
}
