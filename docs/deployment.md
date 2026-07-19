# SmartCS 部署指南

> 中间件（Docker Compose）与编排服务的启动、初始化与验证。

## 目录

- [前置要求](#前置要求)
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

> **Grafana 宿主机端口为 3001**（避免与常见 3000 冲突），容器内仍是 3000。

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
