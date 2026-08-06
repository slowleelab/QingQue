# 灵智（Lumio）配置参考

> 全部环境变量说明。配置经 Pydantic-settings 加载，按前缀分组到子配置类，支持 `.env` 文件或环境变量注入。
>
> 模板见根目录 [`.env.example`](../.env.example)。
>
> 主配置 `Settings` 通过 `@lru_cache` 缓存，经 `get_settings()` 获取。配置类定义见 `agent/lumio/shared/config.py`。

## 加载机制

- 每个子配置类有独立 `env_prefix`（如 `LUMIO_`、`POSTGRES_`、`REDIS_`），共 15 个。
- 重命名后全局前缀从 `SMARTCS_*` 改为 `LUMIO_*`（含 Pydantic `env_prefix`）。
- 数据库用户名 / 数据库名 / Redis 密码见下表。

---

## 全局（`LUMIO_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LUMIO_ENVIRONMENT` | `development` | 运行环境（development / staging / production） |
| `LUMIO_DEBUG` | `true` | 调试开关 |
| `LUMIO_LOG_LEVEL` | `INFO` | 日志级别（DEBUG / INFO / WARNING / ERROR） |
| `LUMIO_SERVICE_HOST` | `127.0.0.1` | 服务监听地址 |
| `LUMIO_JWT_SECRET` | （开发默认） | JWT 签名密钥（生产必须覆盖） |
| `LUMIO_CHAT_SVC_URL` | `http://localhost:8080` | chat-svc customer-server 地址 |
| `LUMIO_ASSIST_URL` | `http://localhost:8001` | 本项目 assist 入口（被 chat-svc agent-server 调用） |
| `LUMIO_CORS_ORIGINS` | `["http://localhost:5173","http://localhost:8080"]` | CORS 允许源（JSON 数组） |

### Bot 服务（`BOT_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `BOT_MAX_CONCURRENT_AGENTS` | `10` | 并发 Agent 上限 |
| `BOT_MESSAGE_TTL_SECONDS` | `8` | 消息队列项过期时间（秒） |
| `BOT_FAST_REPLY_COOLDOWN` | `5` | 快速回复冷却（秒） |
| `BOT_IDLE_TIMEOUT_SECONDS` | `120` | Bot 阶段空闲超时（秒） |

---

## PostgreSQL（`POSTGRES_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `POSTGRES_HOST` | `localhost` | 主机 |
| `POSTGRES_PORT` | `5432` | 端口 |
| `POSTGRES_USER` | `lumio` | 用户（重命名后从 `smartcs` 改为 `lumio`） |
| `POSTGRES_PASSWORD` | `lumio_pass` | 密码（应用连接用） |
| `POSTGRES_DATABASE` | `lumio` | 数据库名（重命名后从 `smartcs` 改为 `lumio`） |
| `POSTGRES_POOL_SIZE` | `10` | SQLAlchemy 连接池大小 |

> 连接串示例：`postgresql+asyncpg://lumio:lumio_pass@localhost:5432/lumio`

## Redis（`REDIS_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `REDIS_HOST` | `localhost` | 主机 |
| `REDIS_PORT` | `6379` | 端口 |
| `REDIS_PASSWORD` | （空） | 密码 |
| `REDIS_DB` | `0` | 逻辑库 |
| `REDIS_MAX_CONNECTIONS` | `20` | 连接池上限 |
| `REDIS_KEY_PREFIX` | `lumio` | 全部 key 前缀（重命名后从 `smartcs` 改为 `lumio`，旧数据可用 RENAME 迁移） |

## Elasticsearch（`ES_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ES_HOSTS` | `http://localhost:9200` | 节点地址（逗号分隔多节点） |
| `ES_USERNAME` | — | 仅生产环境启用 |
| `ES_PASSWORD` | — | 仅生产环境启用 |
| `ES_INDEX_DOCUMENTS` | `lumio_documents` | 文档索引名 |
| `ES_INDEX_FAQ` | `lumio_faq` | FAQ 索引名 |

## Milvus（`MILVUS_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MILVUS_HOST` | `localhost` | 主机 |
| `MILVUS_PORT` | `19530` | 端口 |
| `MILVUS_COLLECTION` | `lumio_chunks` | 集合名 |

## MinIO（`MINIO_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINIO_ENDPOINT` | `localhost:9000` | S3 端点 |
| `MINIO_ACCESS_KEY` | `minioadmin` | Access Key |
| `MINIO_SECRET_KEY` | `minioadmin` | Secret Key |
| `MINIO_BUCKET` | `lumio-documents` | 桶名 |

## Kafka（`KAFKA_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9094` | Broker 列表 |
| `KAFKA_TOPIC_CHAT_QUEUE` | `lumio.chat.queue` | 异步聊天任务队列 |

---

## LLM（`LLM_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI 兼容接口（默认 Ollama） |
| `LLM_API_KEY` | `ollama` | API Key |
| `LLM_PRIMARY_MODEL` | `qwen2.5:7b` | 主模型 |
| `LLM_FALLBACK_MODEL` | `qwen2.5:0.5b` | 降级模型 |
| `LLM_TIMEOUT_SECONDS` | `8` | 单次 LLM 调用超时（秒） |

## MCP / Java 工具（`MCP_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MCP_ENABLED` | `false` | 是否启用 Java MCP 工具层 |
| `MCP_ENDPOINT` | `http://localhost:10000/mcp/credit-card` | streamable-http 入口（Higress 桥接） |
| `MCP_PROGRESSIVE_DISCLOSURE_ENABLED` | `false` | 是否按意图裁剪工具子集 |
| `MCP_PD_CONFIDENCE_THRESHOLD` | `0.7` | 渐进式暴露的意图置信度阈值 |
| `MCP_BACKENDS` | （空） | 多后端配置（JSON 数组） |
| `LUMIO_CREDITCARD_LUHN_CHECK` | `true` | Java 侧卡号 Luhn 校验开关 |

## 坐席辅助（`ASSIST_` / `SESSION_`）

各 OE 执行器超时与节流窗口（毫秒）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ASSIST_SCRIPT_TIMEOUT_MS` | `500` | 话术生成超时 |
| `ASSIST_KNOWLEDGE_TIMEOUT_MS` | `600` | 知识检索超时 |
| `ASSIST_ALERT_TIMEOUT_MS` | `300` | 合规告警超时 |
| `ASSIST_PRODUCT_TIMEOUT_MS` | `400` | 商品推荐超时 |
| `ASSIST_THROTTLE_WINDOW_MS` | `800` | 推送节流窗口 |
| `SESSION_QUEUE_TIMEOUT_SECONDS` | `60` | 排队超时（agent:queued） |
| `SESSION_RINGING_TIMEOUT_SECONDS` | `30` | 振铃超时（agent:assigned） |
| `SESSION_ACTIVE_TIMEOUT_SECONDS` | `1800` | 通话超时（agent:active） |
| `SESSION_REVIEW_TIMEOUT_SECONDS` | `120` | 小结超时（agent:reviewing） |
| `SESSION_FEEDBACK_DELAY_SECONDS` | `3` | 反馈延迟提交窗口（秒） |

## RAG（`RAG_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `RAG_BM25_WEIGHT` | `0.5` | BM25 在 RRF 融合中的权重 |
| `RAG_VECTOR_WEIGHT` | `0.5` | 向量在 RRF 融合中的权重 |
| `RAG_TOP_K` | `20` | 召回 top_k |
| `RAG_RERANK_TOP_K` | `5` | 重排后保留 top_k |

## 安全（`SAFETY_`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SAFETY_PII_MASK_ENABLED` | `true` | 是否对 LLM 输出做 PII 脱敏 |
| `SAFETY_SENSITIVE_WORDS_HOT_RELOAD` | `true` | 是否启用 Pub/Sub 热加载 |

---

## Docker Compose 中间件密码

> 仅被 `deploy/docker-compose.yml` 引用，与应用连接配置相互独立。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `POSTGRES_PASSWORD_DOCKER` | `lumio_pass` | PG 容器初始密码（与 `POSTGRES_PASSWORD` 对齐） |
| `MINIO_ROOT_USER` | `minioadmin` | MinIO root 用户 |
| `MINIO_ROOT_PASSWORD` | `minioadmin` | MinIO root 密码 |
| `GF_ADMIN_USER` | `admin` | Grafana 管理员 |
| `GF_ADMIN_PASSWORD` | `admin` | Grafana 管理员密码 |

---

## 安全提示

- `.env` 已在 `.gitignore` 中，**请勿提交真实凭据**。
- 生产环境务必修改所有默认密码，并通过密钥管理服务注入。
- 金融场景敏感配置（API Key、DB 密码）建议使用环境变量而非文件。
- 历史 `SMARTCS_*` 环境变量已全部失效；如保留旧 `.env` 需替换前缀。

## 相关文档

- [部署指南](./deployment.md) — 中间件端口映射
- [开发指南](./development.md) — 本地配置建议
