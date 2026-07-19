# ── 构建阶段 ──
FROM python:3.11-slim AS builder

WORKDIR /build

# 系统依赖（编译 psycopg2/docling 等 C 扩展）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# 安装 Poetry
RUN pip install --no-cache-dir poetry==1.8.3

# 复制依赖文件
COPY pyproject.toml poetry.lock* ./

# 安装依赖到虚拟环境（不安装项目本身）
RUN poetry config virtualenvs.create false && \
    poetry install --no-dev --no-root

# ── 运行阶段 ──
FROM python:3.11-slim AS runtime

WORKDIR /app

# 运行时系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 && \
    rm -rf /var/lib/apt/lists/*

# 从构建阶段复制已安装的 Python 包
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 复制项目代码
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY alembic/ ./alembic/
COPY alembic.ini ./

# 非 root 用户
RUN useradd -m -u 1000 kp && chown -R kp:kp /app
USER kp

EXPOSE 8100

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8100/health/live')" || exit 1

# 默认启动 API 服务
# Worker 通过 CMD 覆盖: docker run kp-worker python -m app.worker.consumer
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
