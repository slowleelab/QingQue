# 星型长连接微服务系统

基于 Spring Boot 3.1、Netty 4.1 和 ZooKeeper 3.8 实现的星型拓扑长连接微服务系统。

## 系统架构

```
                           ┌─────────────────────────────────┐
                           │          ZooKeeper              │
                           │   /agent-bindings/{agentId}     │
                           │   /customer-bindings/{customerId}│
                           └───────────────┬─────────────────┘
                                           │
        ┌──────────────────────────────────┼──────────────────────────────────┐
        │                                  │                                  │
        ▼                                  ▼                                  ▼
┌───────────────┐                  ┌───────────────┐                  ┌───────────────┐
│ Customer Svr 1│                  │ Customer Svr 2│                  │ Customer Svr N│
│  HTTP: 8080   │                  │  HTTP: 8082   │                  │  HTTP: 80XX   │
│  Netty: 8888  │                  │  Netty: 8890  │                  │  Netty: 88XX  │
└───────┬───────┘                  └───────┬───────┘                  └───────┬───────┘
        │                                  │                                  │
        │         ┌────────────────────────┼────────────────────────┐         │
        │         │                        │                        │         │
        └─────────┼────────────────────────┼────────────────────────┼─────────┘
                  │                        │                        │
                  ▼                        ▼                        ▼
           ┌───────────┐            ┌───────────┐            ┌───────────┐
           │Agent Svr 1│            │Agent Svr 2│            │Agent Svr N│
           │ HTTP:8081 │            │ HTTP:9081 │            │ HTTP:90XX │
           └───────────┘            └───────────┘            └───────────┘
```

### 核心特性

- **星型拓扑架构**: Agent Server 连接所有 Customer Server，支持水平扩展
- **精确消息路由**: 基于ZK绑定关系的消息路由，告别轮询
- **高性能缓存**: 本地TTL缓存 + ZK查询，支持千万级消息量
- **心跳与重连**: 指数退避重连策略，完善的故障转移机制
- **Transport模块**: 可复用的传输层抽象，便于其他应用集成

## 模块说明

```
star-connection/
├── common/                          # 公共模块
│   └── model/                       # 消息定义（Message, Session, Agent, ChatMessage）
│
├── transport/                       # 传输模块
│   ├── transport-core/              # 核心抽象
│   │   ├── connection/              # Connection, ConnectionPool 接口
│   │   ├── heartbeat/               # HeartbeatConfig, HeartbeatManager
│   │   ├── reconnection/            # ReconnectionPolicy, ExponentialBackoffPolicy
│   │   └── cache/                   # BindingCache（TTL缓存）
│   ├── transport-netty/             # Netty实现
│   ├── transport-zookeeper/         # ZK集成
│   └── transport-spring-boot-starter/  # Spring Boot自动配置
│
├── customer-server/                 # 客户服务
│   ├── netty/                       # Netty服务器
│   ├── websocket/                   # 客户WebSocket处理
│   ├── session/                     # 会话管理器
│   ├── zookeeper/                   # CustomerBindingRegistry（客户绑定注册）
│   └── controller/                  # REST API
│
└── agent-server/                    # 坐席服务
    ├── netty/                       # Netty客户端（连接所有Customer Server）
    ├── websocket/                   # 坐席WebSocket处理
    ├── zookeeper/                   # CustomerBindingQuery（客户绑定查询）
    └── controller/                  # REST API
```

## 环境要求

- Java 17+
- Maven 3.9+
- ZooKeeper 3.8+

## 快速开始

### 1. 启动 ZooKeeper

```bash
# Docker方式
docker run --name zookeeper -p 2181:2181 -d zookeeper:3.8
```

### 2. 构建项目

```bash
mvn clean package -DskipTests
```

### 3. 启动客户服务（Customer Server）

```bash
# 节点1
java -jar customer-server/target/customer-server-1.0.0.jar \
  --server.port=8080 \
  --netty.server.port=8888 \
  --router.service-id=router-1

# 节点2（可选）
java -jar customer-server/target/customer-server-1.0.0.jar \
  --server.port=8082 \
  --netty.server.port=8890 \
  --router.service-id=router-2
```

### 4. 启动坐席服务（Agent Server）

```bash
# 节点1
java -jar agent-server/target/agent-server-1.0.0.jar \
  --server.port=8081 \
  --client.service-id=agent-server-1

# 节点2（可选）
java -jar agent-server/target/agent-server-1.0.0.jar \
  --server.port=9081 \
  --client.service-id=agent-server-2
```

### 5. 访问系统

- 监控面板: http://localhost:8080/
- 客户页面: http://localhost:8080/customer.html
- 坐席页面: http://localhost:8081/

## 消息路由机制

### 客户 → 坐席

```
客户消息 → Customer Server
    ↓
查询 session.agentId
    ↓
查询 agentId → agentServerId（本地缓存/ZK）
    ↓
从连接池获取 Agent Server 连接
    ↓
发送到指定 Agent Server → 转发给坐席
```

### 坐席 → 客户

```
坐席消息 → Agent Server
    ↓
查询 sessionId → routerId（SessionStore/缓存/ZK）
    ↓
从连接池获取 Customer Server 连接
    ↓
发送到指定 Customer Server → 转发给客户
```

### ZK绑定关系

```
/star-connection/
├── /agent-bindings/
│   ├── /agent-001 → "agent-server-1"  (临时节点)
│   └── /agent-002 → "agent-server-2"
└── /customer-bindings/
    ├── /customer-001 → "router-1"      (临时节点)
    └── /customer-002 → "router-2"
```

## Transport模块使用

### 添加依赖

```xml
<dependency>
    <groupId>com.example</groupId>
    <artifactId>transport-spring-boot-starter</artifactId>
    <version>1.0.0</version>
</dependency>
```

### 配置

```yaml
transport:
  heartbeat:
    enabled: true
    interval-seconds: 20
    max-missed: 3
  reconnection:
    enabled: true
    initial-delay-ms: 1000
    max-delay-ms: 300000
    max-retries: 10
```

### 使用BindingCache

```java
@Autowired
private BindingCache bindingCache;

// 存入绑定
bindingCache.put("agent-001", "agent-server-1");

// 查询绑定
Optional<String> serverId = bindingCache.get("agent-001");

// 发送失败时失效缓存
bindingCache.invalidate("agent-001");
```

## API端点

### 客户服务 (8080)

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/customer/session` | 创建会话 |
| GET | `/api/customer/session/{sessionId}` | 获取会话 |
| DELETE | `/api/customer/session/{sessionId}` | 关闭会话 |
| GET | `/api/monitor/customer-service/stats` | 客服统计 |
| GET | `/api/monitor/customer-service/sessions` | 会话列表 |
| GET | `/api/monitor/nodes/frontends` | Customer Server节点列表 |
| GET | `/api/monitor/nodes/backends` | Agent Server节点列表 |
| GET | `/api/monitor/zookeeper/metadata` | ZK元数据 |
| WebSocket | `/ws/customer` | 客户连接 |

### 坐席服务 (8081)

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/agent/{agentId}` | 获取坐席 |
| PUT | `/api/agent/{agentId}/status` | 更新状态 |
| WebSocket | `/ws/agent` | 坐席连接 |

## 消息类型

| 类型 | Code | 说明 |
|------|------|------|
| SESSION_CREATE | 10 | 创建会话 |
| SESSION_ASSIGN | 11 | 分配会话（携带routerId） |
| SESSION_CLOSE | 12 | 关闭会话 |
| AGENT_REGISTER | 13 | 坐席注册 |
| AGENT_STATUS | 14 | 坐席状态更新 |
| CHAT_MESSAGE | 15 | 聊天消息 |

## 会话状态机

```
                    ┌───────────┐
                    │  CREATED  │
                    └─────┬─────┘
                          │
                          ▼
     ┌──────────┐    ┌──────────┐    ┌──────────┐
     │ WAITING  │───▶│  ACTIVE  │───▶│  CLOSED  │
     └──────────┘    └──────────┘    └──────────┘
         │               │
         └───────────────┘
            (超时/断开)
```

## 性能优化

### 缓存策略

- **本地缓存**: Guava Cache, TTL 30秒, 最大10万条
- **命中率**: 目标 > 95%
- **失效策略**: TTL过期 + 发送失败主动失效

### 重连策略

```
指数退避 + 抖动：
第1次: 1秒 ± 25%
第2次: 2秒 ± 25%
第3次: 4秒 ± 25%
...
最大: 5分钟
```

## 开发

### 运行测试

```bash
mvn test
```

### 测试覆盖率

```
transport-core: 32 tests
BindingCache: 10 tests
ExponentialBackoffPolicy: 9 tests
HeartbeatConfig: 3 tests
```

## 监控面板

访问 http://localhost:8080/ 查看：

- 会话统计（等待中/进行中）
- 坐席状态（在线/忙碌/离线）
- 系统拓扑图（Customer Server - Agent Server 连接状态）
- 节点指标（内存、CPU、连接数）
- ZK元数据查看

## 许可证

本项目仅供演示使用。
