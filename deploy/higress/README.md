# Higress 生产配置参考

> 本文档仅作参考，开发环境使用 Nginx 替代 Higress。

## Higress 简介

Higress 是阿里云开源的 K8s 原生网关，基于 Istio + Envoy，具备以下 AI 服务治理能力：

- **LLM Proxy**: 大模型请求代理，支持多模型路由和 fallback
- **请求改写**: 网关层注入 system prompt、参数裁剪
- **WAF**: 内置 SQL 注入/XSS/敏感信息泄露防护
- **限流**: 令牌桶/漏桶限流，按模型/路由维度

## K8s 部署

```bash
# 安装 Higress（Helm）
helm repo add higress https://higress.io/helm-charts
helm install higress higress/higress -n higress-system --create-namespace

# 配置 Ingress
kubectl apply -f ingress.yaml
```

## Ingress 配置示例

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: smartcs-ingress
  namespace: smartcs
  annotations:
    # 启用 WebSocket
    higress.io/websocket: "true"
    # 限流：机器人服务 100 QPS
    higress.io/rate-limit: "100/min"
spec:
  ingressClassName: higress
  rules:
  - http:
      paths:
      # 机器人服务
      - path: /api/bot
        pathType: Prefix
        backend:
          service:
            name: bot-service
            port:
              number: 8000
      # 坐席辅助服务
      - path: /api/assist
        pathType: Prefix
        backend:
          service:
            name: assist-service
            port:
              number: 8001
```

## LLM Proxy 配置

```yaml
# Higress AI 路由插件配置
apiVersion: extensions.higress.io/v1alpha1
kind: WasmPlugin
metadata:
  name: llm-proxy
  namespace: higress-system
spec:
  # Qwen2.5 双模型路由
  # 14B 主力 → 7B 降级
  defaultConfig:
    providers:
      - name: qwen-14b
        url: http://vllm-14b:8000/v1/chat/completions
        model: qwen2.5-14b-instruct
      - name: qwen-7b
        url: http://vllm-7b:8000/v1/chat/completions
        model: qwen2.5-7b-instruct
        fallback: true
```
