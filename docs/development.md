# 灵智（Lumio）开发指南

> 本地开发环境、代码规范、测试与工作流。

## 目录

- [环境准备](#环境准备)
- [常用命令](#常用命令)
- [代码规范](#代码规范)
- [测试](#测试)
- [项目结构](#项目结构)
- [开发工作流](#开发工作流)

---

## 环境准备

```bash
# Python 3.11 + Poetry
curl -sSL https://install.python-poetry.org | python3 -

# 安装依赖（务必用 Poetry 管理的虚拟环境）
make install

# 启动中间件
make up && make init

# 安装 pre-commit 钩子
make pre-commit
```

> 注意：项目要求 `^3.11`。若 Poetry 误选了更高版本，请先 `poetry env use python3.11` 再 `make install`。

## 常用命令

| 命令 | 作用 |
|------|------|
| `make install` | 安装依赖（Poetry） |
| `make dev` | 启动 Bot(:8000) + Assist(:8001)，--reload |
| `make test` | 运行 pytest |
| `make test-cov` | 覆盖率测试（≥60%，source = `lumio`） |
| `make lint` | Ruff 检查并自动修复 |
| `make format` | Ruff 格式化 |
| `make type-check` | mypy 类型检查（src = `lumio`） |
| `make pre-commit` | 安装并运行 pre-commit |
| `make up` / `make down` | 启停中间件 |
| `make init` | 初始化 Milvus / ES / Kafka |
| `make verify` | 校验中间件连通性 |
| `make proto` | 编译 gRPC proto（package `lumio`） |
| `make migrate` | 数据库迁移 |
| `make migrate-create msg="..."` | 新建迁移 |
| `make mcp-ref` | 启动参考 MCP Server（`lumio.services.tools.reference_server`） |
| `make mcp-server-run` | 启动 Java MCP Server（端口 8090，profile=dev） |
| `make mcp-server-build` | 构建 Java MCP Server 镜像 |
| `make gateway-up` | 拉起 Higress + Nacos + Java MCP（gateway profile） |
| `make bench` / `make bench-micro` | 跑 Locust 压测 / 微基准（`scripts/bench_micro.py`） |
| `make verify-mcp-e2e` | MCP 工具工程联调 harness |

> 所有 Python 命令经 Poetry 运行：`poetry run <cmd>`。

## 代码规范

| 项 | 约定 |
|----|------|
| 行宽 | 120 |
| Python | 3.11 |
| 引号 | 双引号 |
| Ruff 规则 | E, W, F, I, N, UP, B, A, SIM, RUF |
| isort | `known-first-party = ["lumio"]` |
| mypy | 源码 `disallow_untyped_defs = true`（测试放宽）；`mypy_path = $MYPY_CONFIG_FILE_DIR/src` |
| 模块头 | 每个模块以 `from __future__ import annotations` 开头 |
| 语言 | 用户可见字符串与 docstring 用**中文**；标识符用英文 |
| 异常基类 | `LumioError`（24 个子类，统一错误码） |

**Pre-commit** 会自动执行：ruff（fix）、ruff-format、mypy，以及通用检查（行尾空白、YAML/JSON 校验、大文件、合并冲突、私钥检测）。提交前请确保通过。

## 测试

- **框架**：pytest + pytest-asyncio（`asyncio_mode = "auto"`）
- **Fixtures**：`bot_client` / `assist_client`（httpx.AsyncClient），见 `agent/tests/conftest.py`
- **覆盖率**：≥60%，启用分支覆盖，source = `lumio`
- **测试规模**：当前 728 条测试（其中 688 通过、40 跳过）

```bash
make test                 # 单元/集成测试（不依赖真实中间件的部分）
poetry run pytest tests/test_integration.py -v   # 指定文件
poetry run pytest -q      # 全部（CI 模式）
```

> 部分 E2E/API 测试需要真实中间件（端口可达）。未启动中间件时会以"服务启动超时"标记为 error，属预期；可先 `make up` 再跑。

## 项目结构

```
agent/lumio/              # 主包
  main.py                 # App 工厂 + lifespan（bot_app / assist_app）
  shared/                 # 横切模块（config/exceptions/logger/middleware/models/orm/metrics）
  services/
    bot/                  # Bot 自助服务（app/router/prompts/bot_agent/tool_executor/tool_guard）
    assist/               # 坐席辅助（app/router/ai_executor/summary/alert_engine）
    common/               # 共享基础设施（session/retrieval/ingestion/classifier/
                          #   auth_router/database/deps/...）
    tools/                # reference_server.py（FastMCP mock）
  alembic/                # DB 迁移
  scripts/                # 初始化/验证脚本（init_milvus/elasticsearch/kafka/minio/temporal, verify_*）
  tests/                  # pytest（728 条）
mcp-server/               # Java Spring AI MCP Server（com.lumio.mcp, 22 tools, mock）
chat-svc/          # Java 客户/坐席长连接（customer-server :8080 / agent-server :8081）
web/                      # Vue 3 + TS 前端（/、/agent、/admin、/login）
deploy/                   # docker-compose / Dockerfile / k8s/lumio.yaml / higress/
config/                   # prometheus / grafana（单一事实源）
docs/                     # 技术文档（本目录）
```

## 开发工作流

1. 从 `main` 切功能分支：`git checkout -b feat/xxx`
2. 开发并本地验证：`make lint && make type-check && make test`
3. 提交（pre-commit 自动跑检查）
4. 推 PR，CI 通过（lint + type-check + test）后合入

**分支 / commit 约定**：
- 分支：`feat/`、`fix/`、`refactor/`、`docs/`、`chore/`
- commit：[Conventional Commits](https://www.conventionalcommits.org/)，如 `feat: 新增反馈撤销接口`、`fix: 修复规则热加载 NameError`

详见 [CONTRIBUTING.md](../CONTRIBUTING.md)。

---

## 相关文档

- [架构](./architecture.md) ｜ [API](./api-reference.md) ｜ [部署](./deployment.md) ｜ [配置](./configuration.md) ｜ [基准](./benchmark.md)
