# ── SmartCS 应用 Dockerfile ──
# 多阶段构建：builder 安装依赖 → runtime 仅含虚拟环境 + 应用代码

# ── Stage 1: Builder ──
FROM python:3.11-slim AS builder

WORKDIR /app

# 安装 Poetry
RUN pip install --no-cache-dir poetry==2.0.1

# 先拷贝依赖声明文件（利用 Docker 缓存层）
COPY pyproject.toml poetry.lock* ./

# 安装生产依赖到 .venv（不安装项目本身）
RUN poetry config virtualenvs.in-project true \
    && poetry install --no-interaction --no-ansi --only main --no-root

# ── Stage 2: Runtime ──
FROM python:3.11-slim AS runtime

WORKDIR /app

# 创建非 root 用户
RUN groupadd -r smartcs && useradd -r -g smartcs smartcs

# 从 builder 拷贝虚拟环境
COPY --from=builder /app/.venv /app/.venv

# 拷贝应用代码
COPY src/ /app/src/

# 拷贝配置文件
COPY config/ /app/config/

# 设置环境变量
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src:$PYTHONPATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 切换到非 root 用户
USER smartcs

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

# 默认启动 bot 服务（可通过 CMD 覆盖）
CMD ["uvicorn", "smartcs.main:bot_app", "--host", "0.0.0.0", "--port", "8000"]
