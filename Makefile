# SmartCS Makefile — 标准化开发命令
# 使用: make <target>

.PHONY: help install dev test test-cov lint format type-check build up down init init-minio init-temporal seed seed-dry verify clean migrate migrate-create proto pre-commit verify-ollama

# ── 默认 ──
help: ## 显示帮助信息
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ── AI Agent（Python） ──
install: ## 安装项目依赖（Poetry）
	cd agent && poetry install
	@echo "Generating gRPC stubs..."
	cd agent && poetry run python scripts/generate_grpc.py 2>/dev/null || echo "⚠️  gRPC stubs 生成失败，请手动运行: make proto"

dev: ## 启动开发模式（bot :8000 + assist :8001）
	@echo "Starting bot service on :8000 and assist service on :8001..."
	@cd agent && poetry run uvicorn smartcs.main:bot_app --host 0.0.0.0 --port 8000 --reload &
	@cd agent && poetry run uvicorn smartcs.main:assist_app --host 0.0.0.0 --port 8001 --reload

test: ## 运行测试
	cd agent && poetry run pytest -v

test-cov: ## 运行测试并生成覆盖率报告
	cd agent && poetry run pytest --cov=smartcs --cov-report=term-missing --cov-report=html

lint: ## 代码检查（ruff）
	cd agent && poetry run ruff check . --fix

format: ## 代码格式化（ruff）
	cd agent && poetry run ruff format .

type-check: ## 类型检查（mypy）
	cd agent && poetry run mypy smartcs/ tests/

pre-commit: ## 安装并运行 pre-commit
	cd agent && poetry run pre-commit install
	cd agent && poetry run pre-commit run --all-files

# ── Docker ──
build: ## 构建 Docker 镜像（ES+IK）
	cd deploy && docker compose build elasticsearch

build-app: ## 构建应用 Docker 镜像
	docker build -f deploy/Dockerfile -t smartcs:latest .

up: ## 启动所有中间件
	cd deploy && docker compose up -d

down: ## 停止所有中间件
	cd deploy && docker compose down

ps: ## 查看中间件状态
	cd deploy && docker compose ps

logs: ## 查看中间件日志
	cd deploy && docker compose logs -f

# ── 初始化 ──
init: ## 初始化所有中间件（Milvus + ES + Kafka + Temporal）
	@echo "Initializing middleware..."
	cd agent && poetry run python scripts/init_milvus.py
	cd agent && poetry run python scripts/init_elasticsearch.py
	cd agent && poetry run python scripts/init_kafka.py
	cd agent && poetry run python scripts/init_temporal.py

# ── 验证 ──
verify: ## 验证所有中间件连通性
	cd agent && poetry run python scripts/verify_all.py

verify-ollama: ## 验证 Ollama + Qwen2.5-7B
	cd agent && poetry run python scripts/verify_ollama.py

# ── gRPC ──
proto: ## 编译 gRPC Proto 文件
	cd agent && poetry run python scripts/generate_grpc.py
	@echo "Proto files compiled successfully"

init-temporal: ## 初始化 Temporal
	cd agent && poetry run python scripts/init_temporal.py
	cd agent && poetry run python scripts/generate_grpc.py

# ── 数据库迁移 ──
migrate: ## 运行数据库迁移
	cd agent && poetry run alembic upgrade head

migrate-create: ## 创建新的迁移脚本（用法: make migrate-create msg="add users table"）
	cd agent && poetry run alembic revision --autogenerate -m "$(msg)"

migrate-downgrade: ## 回退一个版本
	cd agent && poetry run alembic downgrade -1

init-minio: ## 初始化 MinIO Bucket
	cd agent && poetry run python scripts/init_minio.py

seed: ## 生成种子知识数据并入库
	cd agent && poetry run python scripts/seed_knowledge.py

seed-dry: ## 扫描种子数据（不入库）
	cd agent && poetry run python scripts/seed_knowledge.py --dry-run

# ── 清理 ──
clean: ## 清理生成文件和缓存
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true

distclean: ## 清理所有（包括 Docker 数据卷）
	cd deploy && docker compose down -v
	rm -rf .venv

# ── 前端（Vue） ──
web-dev: ## 启动前端开发服务器
	cd web && pnpm dev

web-build: ## 构建前端生产版本
	cd web && pnpm build

web-install: ## 安装前端依赖
	cd web && pnpm install

# ── 在线客服（Java） ──
star-build: ## 编译 star-connection
	mvn -f star-connection/pom.xml clean package -DskipTests -q

star-up: ## 启动 star-connection（customer-server :8080 + agent-server :8081）
	java -jar star-connection/customer-server/target/customer-server-1.0.0.jar &
	sleep 3
	java -jar star-connection/agent-server/target/agent-server-1.0.0.jar --server.port=8081 &

star-down: ## 停止 star-connection
	pkill -f "customer-server" || true
	pkill -f "agent-server" || true
