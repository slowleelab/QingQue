# ── Lumio Java MCP Server 生产镜像（多阶段构建）────────────────────
# 红线：本服务仅使用 mock 构造数据，绝不连接真实银行核心系统。
# 阶段一：用 Maven 构建可执行 jar（含 mvn verify 之外的纯打包，CI 已在别处 verify）

FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /workspace
# 先拷贝 pom 以利用依赖层缓存
COPY pom.xml ./
RUN mvn -B -q dependency:go-offline
# 再拷贝源码并打包（跳过测试：CI 的 mcp-server job 已执行 mvn verify）
COPY src ./src
RUN mvn -B -q clean package -DskipTests

# 阶段二：精简运行时镜像（仅 JRE + 非 root 用户）
FROM eclipse-temurin:21-jre AS runtime
LABEL org.opencontainers.image.title="lumio-mcp-server" \
      org.opencontainers.image.description="Lumio 信用卡 MCP Server（mock 数据，生产级骨架）"

# 安装 curl 供健康探针使用，并创建非 root 运行用户
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system lumio \
    && useradd --system --gid lumio --home /app lumio
WORKDIR /app

# 从构建阶段拷贝可执行 jar（finalName=lumio-mcp-server）
COPY --from=build /workspace/target/lumio-mcp-server.jar /app/app.jar
RUN chown -R lumio:lumio /app
USER lumio

EXPOSE 8090

# 容器化 JVM 参数：按 cgroup 限额自适配堆内存
ENV JAVA_OPTS="-XX:MaxRAMPercentage=75.0 -XX:InitialRAMPercentage=50.0"
# 默认启用 prod profile（优雅停机 + 暴露面收敛）
ENV SPRING_PROFILES_ACTIVE="prod"

# 健康探针：actuator health（与 compose/K8s 探活一致）
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD curl -fsS http://localhost:8090/actuator/health || exit 1

ENTRYPOINT ["sh", "-c", "exec java $JAVA_OPTS -jar /app/app.jar"]
