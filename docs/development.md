# SmartCS 开发指南

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

> ⚠️ **Python 版本**：项目要求 `^3.11`。若 Poetry 误选了更高版本，请先 `poetry env use python3.11` 再 `make install`。

## 常用命令

| 命令 | 作用 |
|------|------|
| `make install` | 安装依赖（Poetry） |
| `make dev` | 启动 Bot(:8000) + Assist(:8001)，--reload |
| `make test` | 运行 pytest |
| `make test-cov` | 覆盖率测试（≥60%） |
| `make lint` | Ruff 检查并自动修复 |
| `make format` | Ruff 格式化 |
| `make type-check` | mypy 类型检查 |
| `make pre-commit` | 安装并运行 pre-commit |
| `make up` / `make down` | 启停中间件 |
| `make init` | 初始化 Milvus / ES / Kafka |
| `make verify` | 校验中间件连通性 |
| `make proto` | 编译 gRPC proto |
| `make migrate` | 数据库迁移 |
| `make migrate-create msg="..."` | 新建迁移 |

> 所有 Python 命令经 Poetry 运行：`poetry run <cmd>`。

## 代码规范

| 项 | 约定 |
|----|------|
| 行宽 | 120 |
| Python | 3.11 |
| 引号 | 双引号 |
| Ruff 规则 | E, W, F, I, N, UP, B, A, SIM, RUF |
| isort | `known-first-party = ["smartcs"]` |
| mypy | 源码 `disallow_untyped_defs = true`（测试放宽） |
| 模块头 | 每个模块以 `from __future__ import annotations` 开头 |
| 语言 | 用户可见字符串与 docstring 用**中文**；标识符用英文 |

**Pre-commit** 会自动执行：ruff（fix）、ruff-format、mypy，以及通用检查（行尾空白、YAML/JSON 校验、大文件、合并冲突、私钥检测）。提交前请确保通过。

## 测试

- **框架**：pytest + pytest-asyncio（`asyncio_mode = "auto"`）
- **Fixtures**：`bot_client` / `assist_client`（httpx.AsyncClient），见 `agent/tests/conftest.py`
- **覆盖率**：≥60%，启用分支覆盖，source = `smartcs`

```bash
make test                 # 单元/集成测试（不依赖真实中间件的部分）
poetry run pytest tests/test_integration.py -v   # 指定文件
```

> 部分 E2E/API 测试需要真实中间件（端口可达）。未启动中间件时会以"服务启动超时"标记为 error，属预期；可先 `make up` 再跑。

## 项目结构

```
agent/smartcs/            # 主包
  main.py                 # App 工厂 + lifespan
  shared/                 # 横切模块（config/exceptions/logger/middleware/models/orm）
  services/
    bot/                  # Bot 自助服务（app/router/prompts/bot_agent/knowledge_graph）
    assist/               # 坐席辅助（app/router/agent/arbitrator/executors/...）
    common/               # 共享基础设施（retrieval/embedding/reranker/session/
                          #   degradation/circuit_breaker/audit/pii/auth_router/...）
agent/proto/              # gRPC 定义
agent/scripts/            # 初始化/验证脚本
agent/alembic/            # DB 迁移
agent/tests/              # pytest
config/                   # Prometheus/Grafana（监控单一事实源）
deploy/                   # Docker/nginx/k8s
docs/                     # 技术文档
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

- [架构](./architecture.md) ｜ [API](./api-reference.md) ｜ [部署](./deployment.md) ｜ [配置](./configuration.md)
