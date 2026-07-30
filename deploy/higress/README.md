# Higress + Nacos 接入指南（MCP 单工具平面 · 统一治理）

Higress 是阿里云开源的云原生 AI 网关（Istio + Envoy）。在 Lumio 中，Higress 承担
**「单工具平面 · 单治理」**：把后端各 MCP Server（如 `mcp-server/` 的 22 个信用卡工具）统一收敛，
对上游 Python 编排大脑暴露**一个** streamable-http MCP 入口，并在网关层完成鉴权、限流、审计；
**Nacos** 作为服务发现与 MCP Registry，供 Higress 发现后端 MCP Server。

```
Python 大脑(streamable-http)
        │  MCP_ENDPOINT=http://localhost:10000/mcp/credit-card
        ▼
   ┌─────────────┐   服务发现     ┌──────────┐
   │   Higress   │ ─────────────▶ │  Nacos   │
   │  AI 网关     │                └──────────┘
   │  (统一治理)  │   SSE 代理           ▲ 注册
   └─────┬───────┘                      │
         ▼                              │
   Java MCP Server(:8090, SSE) ─────────┘（profile=nacos 时注册）
   （22 个信用卡工具，mock 数据）
```

> 生产环境 Higress 以 K8s 原生（Helm）部署；本目录提供**开发环境 all-in-one（Docker）**接入，
> 与旧有 Nginx 开发网关并存、互不影响。

## 开发环境快速接入

```bash
# 1) 拉起 Nacos + Higress（仅 gateway profile，不影响 make up 主流程）
make gateway-up

# 2) 启动 Java MCP Server（22 个信用卡工具）
make mcp-server-run
#   如需注册到 Nacos（方式 A），改用：
#   cd mcp-server && mvn spring-boot:run -Dspring-boot.run.profiles=nacos

# 3) 打开 Higress 控制台，导入 MCP Server 路由（见 mcp-credit-card.yaml）
open http://localhost:18080

# 4) 让 Python 大脑经 Higress 调用工具
#    在 .env 中：
#      MCP_ENABLED=true
#      MCP_ENDPOINT=http://localhost:10000/mcp/credit-card

# 停止网关
make gateway-down
```

## 端口

| 组件 | 宿主机端口 | 说明 |
|------|-----------|------|
| Nacos 控制台 / OpenAPI | 8848 | `http://localhost:8848/nacos`（默认账号 nacos/nacos） |
| Nacos gRPC | 9848 | 客户端长连接 |
| Higress 数据面（HTTP） | 10000 | **MCP 入口**：`/mcp/credit-card` |
| Higress 数据面（HTTPS） | 8443 | |
| Higress 控制台 | 18080 | 路由 / MCP / 治理配置 |

## 传输桥接（关键）

- 上游 Python `MCPToolClient` 使用 **streamable-http**（`mcp.client.streamable_http`）。
- 后端 Java MCP Server（Spring AI 1.0.x WebMVC）使用 **SSE**（`/sse` + `/mcp/message`）。
- **Higress 在网关层完成两种传输的桥接**：前端 streamable-http ↔ 后端 SSE，
  因此上游只需连 Higress，无需关心后端传输形态。配置见 `mcp-credit-card.yaml` 的
  `frontendProtocol` / `backendProtocol` 字段。

## 与 Python 侧治理的关系（纵深防御）

Higress 负责**网关层**治理（鉴权、限流、路由、粗粒度审计）；Python 编排层的
确认状态机（敏感工具需用户「确认」）与 `ToolGuard`（按角色授权、金额上限、决策审计）
负责**业务层**治理。两者互补，缺一不可——即便网关放行，敏感写操作仍必须经用户显式确认。

## 生产环境（K8s / Helm）参考

```bash
helm repo add higress https://higress.io/helm-charts
helm install higress higress/higress -n higress-system --create-namespace \
  --set global.mcpRegistry.enabled=true \
  --set global.mcpRegistry.nacos.serverUrl=http://nacos.lumio:8848
```

MCP Server 与网关路由通过 Higress CRD（`McpBridge` / MCP 管理）或控制台声明式下发；
后端服务经 Nacos MCP Registry 发现。业务 Ingress（bot :8000 / assist :8001）示例见下。

### 业务 Ingress 示例

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: lumio-ingress
  namespace: lumio
  annotations:
    higress.io/websocket: "true"       # assist WebSocket
    higress.io/rate-limit: "100/min"
spec:
  ingressClassName: higress
  rules:
    - http:
        paths:
          - path: /api/bot
            pathType: Prefix
            backend: { service: { name: bot-service, port: { number: 8000 } } }
          - path: /api/assist
            pathType: Prefix
            backend: { service: { name: assist-service, port: { number: 8001 } } }
```
