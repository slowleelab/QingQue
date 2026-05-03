# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此代码库中工作时提供指导。

## 项目概述

在线客服平台（Customer Service Platform）是一个基于 Spring Boot 3.1.5、Netty 4.1 和 ZooKeeper 实现的在线客服系统。

### 架构设计

```
用户(浏览器/App)                 坐席(浏览器)
       |                              |
       v                              v
     [HTTP]                     [WebSocket]
       |                              |
       v                              v
+------------------+         +------------------+
|  客户前置(CF)         | <--Netty--> |  坐席后台(AB)       |
| (customer-frontend)|         | (agent-backend) |
+------------------+         +------------------+
```

### 核心功能
- 客户端通过http或http长轮询接入"客户前置"， http长轮询为客户端接收消息的方式
- "坐席后台"启动后后主动和每一个"客户前置"建立netty长连接
- 坐席登录后，通过负载均和其中一个"坐席后端"节点建立websocket连接，并将坐席websocket连接绑定关系注册到zk，用于后面的消息路由
- 会话状态机（WAITING → ACTIVE → CLOSED）
- 客户进线后，坐席负载均衡（最少连接数优先），建立会话
- 消息路由与转发，已建立会话后，通过会话绑定的坐席，结合坐席websocket连接绑定的信息将消息从"客户前置"路由转发到"坐席后端"

## 构建命令

```bash
# 构建所有模块
mvn clean package

# 跳过测试构建
mvn clean package -DskipTests

# 运行测试
mvn test

# 清理项目
mvn clean
```

## 运行系统

**前置条件:** ZooKeeper 运行在 localhost:2181

```bash
# 启动 ZooKeeper（Docker）
docker run --name zookeeper -p 2181:2181 -d zookeeper:3.8

# 1. 启动客户前置（HTTP 端口 8080，Netty 端口 8888）
java -jar customer-frontend/target/customer-frontend-1.0.0.jar

# 2. 启动坐席后台（HTTP 端口 8081）
java -jar agent-backend/target/agent-backend-1.0.0.jar --server.port=8081
java -jar agent-backend/target/agent-backend-1.0.0.jar --server.port=8082 --agent-backend.service-id=agent-backend-2
```

## 模块说明

三个 Maven 模块：

- **common**: 共享消息模型和编解码器
  - `Message`, `MessageType` - 消息实体
  - `Session`, `SessionStatus` - 会话实体
  - `Agent`, `AgentStatus` - 坐席实体
  - `ChatMessage`, `SenderType` - 聊天消息

- **customer-frontend**: 客户前置
  - 客户 WebSocket 端点 (`/ws/customer`)
  - 会话管理与会话状态机
  - 坐席分配
  - 消息路由到坐席后台

- **agent-backend **: 坐席后台
  - 坐席 WebSocket 端点 (`/ws/agent`)
  - 坐席管理与状态维护
  - 连接到web前端进行消息转发

## 消息类型

| 类型 | Code | 说明 |
|------|------|------|
| SESSION_CREATE | 10 | 创建会话 |
| SESSION_ASSIGN | 11 | 分配会话给坐席 |
| SESSION_CLOSE | 12 | 关闭会话 |
| AGENT_REGISTER | 13 | 坐席注册 |
| AGENT_STATUS | 14 | 坐席状态更新 |
| CHAT_MESSAGE | 15 | 聊天消息 |

## 会话状态机

```
WAITING ──(分配坐席)──> ACTIVE ──(关闭)──> CLOSED
    │                      │
    └──(超时/断开)─────────┘
```

**状态转换事件：**
- `CREATE` - 创建会话
- `ASSIGN_AGENT` - 分配坐席
- `CUSTOMER_MESSAGE` - 客户消息
- `AGENT_MESSAGE` - 坐席消息
- `CUSTOMER_DISCONNECT` - 客户断开
- `AGENT_DISCONNECT` - 坐席断开
- `TIMEOUT` - 会话超时
- `CLOSE` - 手动关闭
- `TRANSFER` - 转接会话

## 关键配置

- **客户前置:** `customer-frontend/src/main/resources/application.yml`
  - HTTP 端口: 8080
  - Netty 端口: 8888
  - WebSocket 路径: `/ws/customer`

- **坐席后台:** `agent-backend/src/main/resources/application.yml`
  - HTTP 端口: 8081
  - WebSocket 路径: `/ws/agent`

- **ZooKeeper:** `zookeeper.connect-string: localhost:2181`

## API 端点

### 客户前端 (8080)
- `POST /api/customer/session` - 创建会话
- `GET /api/customer/session/{sessionId}` - 获取会话信息
- `DELETE /api/customer/session/{sessionId}` - 关闭会话
- `GET /api/monitor/customer-service/stats` - 客服统计
- `GET /api/monitor/customer-service/sessions` - 活跃会话列表
- `GET /api/monitor/customer-service/agents` - 坐席列表

### 坐席后台 (8081)
- `GET /api/agent/{agentId}` - 获取坐席信息
- `PUT /api/agent/{agentId}/status` - 更新坐席状态
- `GET /api/agent/{agentId}/sessions` - 坐席会话列表

## 监控面板

访问 `http://localhost:8080` 查看监控面板：

- 会话统计（等待中/进行中）
- 坐席状态（在线/忙碌/离线）
- 最近10条活跃会话列表，支持按时间、会话号、坐席id查询
- "坐席后台"节点列表，点击其中一个节点可以查看详情，详情展示坐席连接负载情况
- 系统实时网络拓扑图

## 技术栈

- Java 17, Maven 3.9+, Spring Boot 3.1.5
- Netty 4.1.100.Final（NIO 网络通信）
- Spring WebSocket（浏览器 WebSocket）
- Apache Curator 5.5.0（ZooKeeper 客户端）
- Jackson 2.15.2（JSON 序列化）
