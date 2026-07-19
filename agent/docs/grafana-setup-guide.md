# SmartCS Grafana 监控对接手册

> **版本**: v1.0 | **日期**: 2026-05-25

---

## 一、架构概览

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ Bot (:8000)  │    │Assist(:8001) │    │ Redis/ES/    │
│ /metrics     │    │ /metrics     │    │ Postgres     │
└──────┬───────┘    └──────┬───────┘    └──────┬───────┘
       │                   │                   │
       ▼                   ▼                   ▼
┌─────────────────────────────────────────────────┐
│              Prometheus (:9090)                  │
│         采集间隔: 10-15s                          │
└─────────────────────┬───────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│              Grafana (:3001)                     │
│          Dashboard: SmartCS 服务监控              │
└─────────────────────────────────────────────────┘
```

## 二、访问入口

| 组件 | 地址 | 默认账号 |
|------|------|---------|
| Grafana | `http://localhost:3001` | admin / admin |
| Prometheus | `http://localhost:9090` | 无认证 |
| Bot 指标 | `http://localhost:8000/metrics` | — |
| Nginx 统一监控页 | `http://localhost:8080/monitor.html` | — |

## 三、Prometheus 采集配置

配置文件位于 `deploy/prometheus/prometheus.yml`，已预配置以下采集目标：

```yaml
scrape_configs:
  - job_name: "bot-service"
    metrics_path: /metrics
    static_configs:
      - targets: ["host.docker.internal:8000"]
    scrape_interval: 10s

  - job_name: "assist-service"
    metrics_path: /metrics
    static_configs:
      - targets: ["host.docker.internal:8001"]
    scrape_interval: 10s

  - job_name: "redis"
    static_configs:
      - targets: ["redis-exporter:9121"]

  - job_name: "postgres"
    static_configs:
      - targets: ["postgres-exporter:9187"]
```

**新增采集目标**：在 `scrape_configs` 中添加新的 `job_name` 块，执行 `docker exec smartcs-prometheus kill -HUP 1` 热加载。

## 四、Grafana Dashboard

### 预置面板

| 面板 | 类型 | 指标 | 说明 |
|------|------|------|------|
| HTTP 请求速率 | Stat | `rate(http_requests_total[1m])` | 每秒请求数 |
| Agent 槽位利用率 | Gauge | `smartcs_bot_semaphore_utilization` | 0-100%，>80% 黄色，>90% 红色 |
| PEL 待处理消息 | Stat | `smartcs_stream_pending_total` | Redis Streams 未确认消息数 |
| 活跃 Worker 数 | Stat | `smartcs_active_workers` | 当前活跃的 per-session Worker |
| 请求延迟 P50/P99 | TimeSeries | `http_request_duration_seconds` | HTTP 响应延迟分位数 |
| 会话阶段转换 | TimeSeries | `session_transitions_total` | BOT→AGENT→ENDED 转换速率 |
| 快速兜底 vs 标准 Agent | TimeSeries | `fast_reply_total` / `agent_responses_total` | 过载保护触发频率 |
| 会话超时事件 | TimeSeries | `session_timeouts_total` | 各子阶段超时速率 |
| Stream 长度趋势 | TimeSeries | `smartcs_stream_length` | 消息队列堆积情况 |
| LLM 调用延迟 | TimeSeries | `llm_call_duration_seconds` | LLM 调用 P50/P99 |

### 导入方式

**方式一：API 导入（已执行）**
```bash
curl -X POST "http://admin:admin@localhost:3001/api/dashboards/db" \
  -H "Content-Type: application/json" \
  -d "{\"dashboard\":$(cat config/grafana/dashboards/smartcs-overview.json),\"overwrite\":true}"
```

**方式二：UI 手动导入**
1. 打开 `http://localhost:3001` → 登录
2. 左侧菜单 → Dashboards → New → Import
3. 上传 `config/grafana/dashboards/smartcs-overview.json`

### 访问 Dashboard
- URL: `http://localhost:3001/d/smartcs-overview`
- 自动刷新: 10 秒

## 五、关键告警规则

在 Prometheus 中添加告警规则（`deploy/prometheus/alert.rules.yml`）：

```yaml
groups:
  - name: smartcs
    rules:
      - alert: BotServiceDown
        expr: up{job="bot-service"} == 0
        for: 1m
        labels: {severity: critical}
        annotations: {summary: "Bot 服务不可用"}

      - alert: HighSemaphoreUtilization
        expr: smartcs_bot_semaphore_utilization > 0.8
        for: 5m
        labels: {severity: warning}
        annotations: {summary: "Agent 槽位利用率超过 80%"}

      - alert: PELBacklog
        expr: smartcs_stream_pending_total > 50
        for: 5m
        labels: {severity: warning}
        annotations: {summary: "Redis Streams PEL 积压超过 50"}

      - alert: HighFastReplyRate
        expr: rate(smartcs_fast_reply_total[5m]) / rate(http_requests_total[5m]) > 0.5
        for: 5m
        labels: {severity: warning}
        annotations: {summary: "快速兜底占比超过 50%，建议扩容"}
```

## 六、自定义指标接入

在代码中添加新指标（`agent/smartcs/shared/metrics.py`）：

```python
from prometheus_client import Counter, Histogram, Gauge

NEW_METRIC = Counter("smartcs_new_metric_total", "新指标说明", ["label1"])
```

应用自动通过 `/metrics` 端点暴露，Prometheus 下一个采集周期自动发现。

## 七、故障排查

| 问题 | 排查方法 |
|------|---------|
| Dashboard 无数据 | 检查 Prometheus targets: `http://localhost:9090/targets` |
| 指标名不匹配 | 检查原始指标: `curl http://localhost:8000/metrics \| grep smartcs` |
| Grafana 无法启动 | `docker logs smartcs-grafana` |
| Prometheus 采集失败 | `docker logs smartcs-prometheus` |

---

## 八、全链路追踪 (Jaeger + OpenTelemetry)

### 架构

```
请求进入 → trace_id 注入
    │
    ├── Nginx (:8080)       [无探针，通过 header 传播]
    ├── Bot (:8000)          [FastAPI + Redis 探针]
    │     ├── Agent.run()    [手动 Span]
    │     ├── LLM 调用       [HTTPX 探针]
    │     └── Redis 操作     [Redis 探针]
    └── Assist (:8001)       [FastAPI + Redis 探针]
          └── OE 编排        [手动 Span]

所有 Span 上报到 Jaeger (:4318) → UI 查看 (:16686)
```

### 启动 Jaeger

```bash
docker run -d --name smartcs-jaeger \
  -p 16686:16686 -p 4317:4317 -p 4318:4318 \
  jaegertracing/all-in-one:latest
```

### 访问

| 组件 | 地址 |
|------|------|
| Jaeger UI | `http://localhost:16686` |
| OTLP 端点 | `http://localhost:4318/v1/traces` |

### 启用/禁用追踪

环境变量控制：
```bash
SMARTCS_TRACING_ENABLED=true   # 启用 (默认)
SMARTCS_TRACING_ENABLED=false  # 禁用
JAEGER_HOST=localhost           # Jaeger 地址
```

### Jaeger 使用

1. 打开 `http://localhost:16686`
2. 选择 Service: `smartcs-bot` 或 `smartcs-assist`
3. 点击 Find Traces 查看请求链路
4. 点击单个 Trace 查看每步耗时：
   - `POST /api/chat/send` → XADD → Agent.run() → LLM → SETEX → PUBLISH
   - `POST /api/notify` → Queue → classify() → OE → WS push

### 安装依赖

```bash
pip install opentelemetry-api opentelemetry-sdk \
  opentelemetry-exporter-otlp-proto-http \
  opentelemetry-instrumentation-fastapi \
  opentelemetry-instrumentation-redis \
  opentelemetry-instrumentation-httpx
```

### 故障排查

| 问题 | 排查方法 |
|------|---------|
| Jaeger 无数据 | 检查 OTLP 端点: `curl http://localhost:4318/v1/traces` |
| 服务未出现在 Jaeger | 检查 `SMARTCS_TRACING_ENABLED=true`，查看应用启动日志 |
| Span 缺失 | 检查所需 opentelemetry-instrumentation-* 包是否安装 |
