# SmartCS 配置参考

> 全部环境变量说明。配置经 Pydantic-settings 加载，按前缀分组到子配置类，支持 `.env` 文件或环境变量注入。
>
> 模板见根目录 [`.env.example`](../.env.example)。

## 加载机制

- 每个子配置类有独立 `env_prefix`（如 `POSTGRES_`、`REDIS_`），共 15 个。
- 主配置 `Settings` 通过 `@lru_cache` 缓存，经 `get_settings()` 获取。
- 配置类定义见 `agent/smartcs/shared/config.py`。

## 变量总览

### 全局（`SMARTCS_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SMARTCS_ENVIRONMENT` | `development` | 运行环境（development/staging/production） |
| `SMARTCS_DEBUG` | `true` | 调试开关 |
| `SMARTCS_LOG_LEVEL` | `INFO` | 日志级别 |
| `SMARTCS_SERVICE_HOST` | `127.0.0.1` | 服务监听地址 |
| `SMARTCS_STAR_CONNECTION_URL` | `http://localhost:8080` | star-connection 接入地址 |
| `SMARTCS_CORS_ORIGINS` | `["http://localhost:5173","http://localhost:8080"]` | CORS 允许源（JSON 数组） |

### Bot 服务（`BOT_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `BOT_MAX_CONCURRENT_AGENTS` | `10` | 并发 Agent 上限 |
| `BOT_MESSAGE_TTL_SECONDS` | `8` | 消息队列项过期时间（秒） |
| `BOT_FAST_REPLY_COOLDOWN` | `5` | 快速回复冷却（秒） |

### PostgreSQL（`POSTGRES_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `POSTGRES_HOST` | `localhost` | 主机 |
| `POSTGRES_PORT` | `5432` | 端口 |
| `POSTGRES_USER` | `smartcs` | 用户 |
| `POSTGRES_PASSWORD` | `smartcs_pass` | 密码（应用连接用） |
| `POSTGRES_DATABASE` | `smartcs` | 数据库名 |

### Redis（`REDIS_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `REDIS_HOST` | `localhost` | 主机 |
| `REDIS_PORT` | `6379` | 端口 |
| `REDIS_PASSWORD` | （空） | 密码 |
| `REDIS_DB` | `0` | 逻辑库 |
| `REDIS_MAX_CONNECTIONS` | `20` | 连接池上限 |

### Elasticsearch（`ES_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ES_HOSTS` | `http://localhost:9200` | 节点地址（逗号分隔多节点） |
| `ES_USERNAME` | — | 仅生产环境启用 |
| `ES_PASSWORD` | — | 仅生产环境启用 |

### Milvus（`MILVUS_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MILVUS_HOST` | `localhost` | 主机 |
| `MILVUS_PORT` | `19530` | 端口 |

### MinIO（`MINIO_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINIO_ENDPOINT` | `localhost:9000` | S3 端点 |
| `MINIO_ACCESS_KEY` | `minioadmin` | Access Key |
| `MINIO_SECRET_KEY` | `minioadmin` | Secret Key |

### Kafka（`KAFKA_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9094` | Broker 列表 |

### LLM（`LLM_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI 兼容接口（默认 Ollama） |
| `LLM_API_KEY` | `ollama` | API Key |
| `LLM_PRIMARY_MODEL` | `qwen2.5:7b` | 主模型 |
| `LLM_FALLBACK_MODEL` | `qwen2.5:0.5b` | 降级模型 |

### gRPC 服务（`CLS_` / `RAG_` / `SAFETY_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CLS_GRPC_HOST` / `CLS_GRPC_PORT` | `localhost:50051` | 分类服务 |
| `RAG_GRPC_HOST` / `RAG_GRPC_PORT` | `localhost:50052` | 检索服务 |
| `SAFETY_GRPC_HOST` / `SAFETY_GRPC_PORT` | `localhost:50053` | 安全过滤服务 |

### 坐席辅助（`ASSIST_`）

各 OE 执行器超时与节流窗口（毫秒）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ASSIST_SCRIPT_TIMEOUT_MS` | `500` | 话术生成超时 |
| `ASSIST_KNOWLEDGE_TIMEOUT_MS` | `600` | 知识检索超时 |
| `ASSIST_ALERT_TIMEOUT_MS` | `300` | 合规告警超时 |
| `ASSIST_PRODUCT_TIMEOUT_MS` | `400` | 商品推荐超时 |
| `ASSIST_THROTTLE_WINDOW_MS` | `800` | 推送节流窗口 |

### Docker Compose 中间件密码

> 仅被 `deploy/docker-compose.yml` 引用，与应用连接配置相互独立。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `POSTGRES_PASSWORD_DOCKER` | `smartcs_pass` | PG 容器初始密码 |
| `MINIO_ROOT_USER` | `minioadmin` | MinIO root 用户 |
| `MINIO_ROOT_PASSWORD` | `minioadmin` | MinIO root 密码 |
| `GF_ADMIN_USER` | `admin` | Grafana 管理员 |
| `GF_ADMIN_PASSWORD` | `admin` | Grafana 管理员密码 |

## 安全提示

- `.env` 已在 `.gitignore` 中，**请勿提交真实凭据**。
- 生产环境务必修改所有默认密码，并通过密钥管理服务注入。
- 金融场景敏感配置（API Key、DB 密码）建议使用环境变量而非文件。

## 相关文档

- [部署指南](./deployment.md) — 中间件端口映射
- [开发指南](./development.md) — 本地配置建议
