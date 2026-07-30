# SmartCS 部署指南

> 中间件（Docker Compose）与编排服务的启动、初始化与验证。

## 目录

- [前置要求](#前置要求)
- [一键 Demo](#一键-demo)
- [快速启动](#快速启动)
- [中间件清单与端口](#中间件清单与端口)
- [初始化](#初始化)
- [启动编排服务](#启动编排服务)
- [验证](#验证)
- [监控](#监控)
- [常见问题](#常见问题)

---

## 前置要求

| 依赖 | 版本 | 用途 |
|------|------|------|
| Docker / Docker Compose | 24+ | 全部中间件 |
| Python | 3.11 | 编排服务（agent / knowledge-platform） |
| Poetry | 1.7+ | Python 依赖管理 |
| Node / pnpm | 20+ / 9+ | 前端（web/，可选） |
| Ollama | 最新 | 本地 LLM（Qwen2.5-7B），可选 |

## 一键 Demo

最快体验方式：在 `docker-compose.yml` 之上叠加 `docker-compose.demo.yml`，额外拉起一个一次性的 **demo-init** 容器（执行 `alembic upgrade head` + 知识库种子数据脚本，幂等可重复执行），随后以容器内主机名启动 Bot/Assist 服务。全程无需本地 Python/Poetry 环境，仅需 Docker。

```bash
make demo        # 构建镜像并启动：中间件 + demo-init + bot:8000 + assist:8001
make demo-ps     # 查看状态（bot/assist 变为 healthy 即就绪）
make demo-logs   # 跟踪日志
make demo-down   # 停止并清理
```

启动后体验：

```bash
curl -X POST http://localhost:8000/api/chat/send \
  -H 'Content-Type: application/json' \
  -d '{"message":"信用卡年费怎么减免"}'
```

说明：

- Demo 编排文件：`deploy/docker-compose.demo.yml`（override，不改动基础编排）。
- LLM 默认指向宿主机 Ollama（`http://host.docker.internal:11434/v1`）；不可达时系统自动降级（检索摘要 → 模板回复），不影响流程演示。可通过环境变量覆盖：`LLM_BASE_URL=http://your-llm/v1 make demo`。
- 镜像构建上下文为 `agent/`（见 `deploy/Dockerfile`），首次构建约需几分钟。

## 快速启动

```bash
# 1. 克隆后配置环境变量
cp .env.example .env          # 按需修改

# 2. 启动全部中间件
make up                        # = docker compose -f deploy/docker-compose.yml up -d

# 3. 初始化（Milvus 集合 + ES 索引 + Kafka topic）
make init

# 4. 安装依赖并启动 Bot(:8000) + Assist(:8001)
make install
make dev
```

## 中间件清单与端口

`deploy/docker-compose.yml` 编排以下服务：

| 服务 | 镜像 | 端口（宿主机:容器） | 用途 |
|------|------|---------------------|------|
| postgres | postgres:16 | 5432:5432 | 业务真相源 |
| redis | redis:7.2-alpine | 6379:6379 | 会话/缓存/Pub-Sub |
| elasticsearch | smartcs/elasticsearch-ik:8.19.9 | 9200:9200, 9300:9300 | 全文检索（IK 分词） |
| etcd | quay.io/coreos/etcd:v3.5.5 | — | Milvus 元数据 |
| minio | minio/minio | 9000:9000, 9001:9001 | 对象存储（9001 控制台） |
| milvus | milvusdb/milvus:v2.4.0 | 19530:19530, 9091:9091 | 向量检索 |
| kafka | apache/kafka:3.7.0 | 9092:9092, 9094:9094 | 消息队列（KRaft） |
| zookeeper | zookeeper:3.8 | 2182:2181 | Kafka 协调 |
| temporal | — | — | 工作流引擎 |
| redis-exporter | oliver006/redis_exporter | 9121:9121 | Redis 指标 |
| postgres-exporter | postgres-exporter | 9187:9187 | PG 指标 |
| kafka-exporter | danielqsj/kafka-exporter | 9308:9308 | Kafka 指标 |
| prometheus | prom/prometheus:v2.50.0 | 9090:9090 | 指标聚合 |
| grafana | grafana/grafana:10.4.0 | **3001**:3000 | 监控看板 |
| nginx | nginx:1.25-alpine | 8080:80 | 接入层 |
| nacos | nacos/nacos-server:v2.4.3 | 8848:8848, 9848:9848 | 服务发现 + MCP Registry（`gateway` profile，默认不启动） |
| higress | higress/all-in-one:2.1.5 | 10000:80, 8443:443, 18080:8080 | AI 网关统一 MCP 数据面 + 控制台（`gateway` profile，默认不启动） |
| mcp-server | smartcs-mcp-server:1.0.0（本地构建） | 8090:8090 | Java 信用卡 MCP 工具服务（mock 数据，`gateway` profile，默认不启动） |

> **Grafana 宿主机端口为 3001**（避免与常见 3000 冲突），容器内仍是 3000。

## AI 网关（Higress + Nacos，可选）

`nacos`、`higress` 与 `mcp-server` 归入 Docker Compose 的 `gateway` profile，**默认 `make up` 不启动**，对现有部署零回归。仅当启用 MCP 工具层（`MCP_ENABLED=true`）、需要经统一治理平面调用信用卡工具时才拉起：

```bash
# 1. 启动 Higress + Nacos + Java MCP Server（opt-in profile，mcp-server 随之构建并注册到 Nacos）
make gateway-up

# 2.（可选）若需本地直连联调而非容器化：以 nacos profile 手动运行
cd mcp-server && mvn spring-boot:run -Dspring-boot.run.profiles=nacos
#   或直接：make mcp-server-run（不注册，仅本地 SSE 直连联调）

# 3. 校验网关连通性（MCP_ENABLED=true 时才实测，否则自动跳过保持全绿）
make verify
```

> `gateway` profile 下的 `mcp-server` 服务以 `prod,nacos` profile 启动：优雅停机 + actuator 暴露面收敛 + 注册到 Nacos（实例 IP = 容器服务名 `mcp-server`），由 Higress 经服务发现路由。镜像来自 `mcp-server/Dockerfile`（多阶段、非 root、健康探针），首次 `make gateway-up` 会自动构建。

架构（单平面·单治理）：

```
Python 编排大脑（bot_agent → ToolCallingExecutor → MCPToolClient）
        │ streamable-http  MCP_ENDPOINT=http://localhost:10000/mcp/credit-card
        ▼
   Higress AI 网关（限流 / 鉴权 / 工具审计；SSE ↔ streamable-http 桥接）
        │ 经 Nacos 服务发现
        ▼
   Java MCP Server（:8090 /sse，Spring AI，22 个信用卡工具，mock 数据）
```

| 组件 | 宿主机端口 | 说明 |
|------|-----------|------|
| Nacos 控制台 | 8848 | `http://localhost:8848/nacos`（默认 nacos/nacos） |
| Nacos gRPC | 9848 | 客户端长连接 |
| Higress MCP 入口 | 10000 | Python 客户端统一 MCP 数据面（streamable-http） |
| Higress 控制台 | 18080 | 路由 / 治理策略管理 |

> 传输差异：Spring AI 1.0.x 的 WebMVC MCP Server 走 **SSE**（`/sse` + `/mcp/message`），Python `MCPToolClient` 走 **streamable-http**，两者由 Higress 桥接。路由参考配置见 `deploy/higress/mcp-credit-card.yaml`，治理与红线说明见 `deploy/higress/README.md`。

### 端到端联调验证（不依赖 Higress / Docker）

`make verify-mcp-e2e`（脚本 `agent/scripts/verify_mcp_e2e.py`）用两条互补链路验证 MCP 工具工程可用性：

1. **阶段 1 — Java 直连**：Python `mcp` SSE 客户端直连 `http://localhost:8090/sse`，断言 **22 个工具**并跑只读 / 写 / 幂等 / 业务错误代表性用例。
2. **阶段 2 — 渐进式暴露**：参考 MCP Server（进程内内存传输）↔ `MCPToolClient` ↔ `ToolCallingExecutor`，开启渐进式暴露后按意图裁剪工具子集，交真实 LLM 自主调用。
3. **阶段 0 — 静态一致性**：校验 `intent_tool_map` 引用的工具名都存在于工具目录。

harness **友好降级**：缺 live Java（:8090）或本地 LLM（Ollama :11434）时相关阶段判定为 SKIP 并给出启动指引，仅硬性契约（工具数、幂等、错误、渐进式裁剪）失败才以非零码退出；全程仅连接 mock / 参考工具，绝不触达真实银行系统。

```bash
make mcp-server-build && make mcp-server-run   # 另开终端启动 Java :8090
make verify-ollama                             # 可选：本地 LLM 就绪
make verify-mcp-e2e                            # 运行联调 harness
```

### 渐进式工具暴露（Progressive Disclosure）

工具规模扩到 22 个后，一次性把全部工具塞给 LLM 会增加选择噪声与 token 成本。`MCP_PROGRESSIVE_DISCLOSURE_ENABLED=true` 开启后，bot_agent 在命中「查询类工具意图」（账单 / 交易 / 额度 / 分期 / 积分）且意图置信度 ≥ `MCP_PD_CONFIDENCE_THRESHOLD`（默认 0.7）时，只向 LLM 暴露该意图对应的工具子集（`MCP_INTENT_TOOL_MAP`），其余情况回落全量或知识问答（RAG）。

**零回归**：默认关闭；关闭时 bot 路由与工具暴露与现状逐字一致。该能力是 host 层策略，与网关模式、多后端拓扑正交。

### 路由模式：多 MCP 后端

`MCPToolClient` 支持连接多个 MCP 后端并在 host 侧合并工具目录：每个后端按 `prefix` 生成域命名空间工具名（如 `card.query_card_bill`）防撞名，并建立 `name→(server, raw_name)` 分发索引，调用时去前缀后派发到对应后端。经 `MCP_BACKENDS`（JSON 数组）配置：

```
                       ┌─ 后端 A（prefix "card."）→ 账单/额度/分期…
MCPToolClient ─合并目录─┤
   (name→server 分发)   └─ 后端 B（prefix "pts.")  → 积分/权益…
```

- **优雅降级**：某后端连接或列举失败时仅其工具缺席，其余后端与主链路不受影响；对应意图自然回落知识问答或转人工。
- **零回归**：`MCP_BACKENDS` 留空 → 退回单后端（用 `MCP_ENDPOINT`、空前缀、工具名与 schema 契约不变）。


## 初始化

```bash
make init
```

执行 `agent/scripts/` 下的初始化脚本：

- `init_elasticsearch.py` — 创建 ES 索引（IK 分词映射）
- `init_milvus.py` — 创建 Milvus 集合与向量索引
- Kafka topic 创建
- `init_temporal.py` — Temporal namespace / 工作流注册

数据库表结构迁移：

```bash
make migrate                # Alembic 升级
make migrate-create msg="..."  # 新建迁移
```

## 启动编排服务

```bash
make dev        # 同时启动 Bot(:8000) + Assist(:8001)，--reload 热重载
```

或分别启动（在 `agent/` 目录）：

```bash
poetry run uvicorn smartcs.main:create_bot_app --factory --port 8000 --reload
poetry run uvicorn smartcs.main:create_assist_app --factory --port 8001 --reload
```

## 验证

```bash
make verify     # 校验各中间件连通性
```

服务健康检查：

```bash
curl http://localhost:8000/api/health    # Bot
curl http://localhost:8001/api/health    # Assist
```

## 监控

- Prometheus：<http://localhost:9090>
- Grafana：<http://localhost:3001>（默认账号见 `.env` 的 `GF_ADMIN_USER` / `GF_ADMIN_PASSWORD`）
- 看板与数据源已 provisioning 自动加载，配置文件在 [`config/grafana/`](../config/grafana/)

## 常见问题

**Q: ES 启动报分词器错误？**
A: 必须使用带 IK 分词器的镜像 `smartcs/elasticsearch-ik`（由 `deploy/elasticsearch/Dockerfile` 构建）。

**Q: Milvus 连接失败？**
A: Milvus 依赖 etcd 与 MinIO，需等其依赖健康后再启动；`docker compose up` 已配置依赖顺序，个别机器首次启动较慢。

**Q: 没有本地 LLM 如何体验？**
A: 配置 `LLM_BASE_URL` 指向兼容 OpenAI 的接口，或利用内置降级路径（检索 + 模板回复）。

**Q: 端口冲突？**
A: 修改 `deploy/docker-compose.yml` 端口映射，或调整 `.env` 中对应 `*_PORT`。

---

## 相关文档

- [配置参考](./configuration.md) — 全部环境变量
- [系统架构](./architecture.md) — 组件关系
