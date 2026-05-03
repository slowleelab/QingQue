# 星型长连接微服务系统设计文档

## 1. 系统概述

### 1.1 项目背景
本项目旨在构建一个基于星型拓扑的长连接微服务通信系统，用于微服务之间的实时信息传递。系统采用路由节点作为消息路由枢纽，多个客户端节点通过长连接与路由节点通信。

### 1.2 设计目标
- 实现微服务间的实时消息传递
- 支持服务动态注册与发现
- 保证连接的高可用性和可靠性
- 提供简单易用的API接口
- 支持水平扩展

## 2. 系统架构

### 2.1 架构图
```
┌─────────────────────────────────────────────────────┐
│                   ZooKeeper集群                      │
│            (服务注册与发现，连接状态协调)                │
└─────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────┐
│                   路由节点 (Router Node)              │
│         (Netty服务器，消息路由，连接管理)               │
└─────────────────────────────────────────────────────┘
                            │
    ┌───────────┐  ┌───────────┐  ┌───────────┐
    │ 客户端节点 │  │ 客户端节点 │  │ 客户端节点 │
    │  Client 1 │  │  Client 2 │  │  Client N │
    └───────────┘  └───────────┘  └───────────┘
```

### 2.2 组件说明
1. **ZooKeeper集群**: 负责服务注册与发现，维护服务元数据
2. **路由节点**: Netty服务器，作为消息路由中心，管理所有客户端连接
3. **客户端节点**: Netty客户端，微服务实例，通过长连接与路由节点通信

## 3. 技术栈

### 3.1 核心框架
- **Spring Boot 3.1.5**: 微服务框架
- **Netty 4.1.100.Final**: 高性能网络通信框架
- **ZooKeeper 3.8.3**: 分布式协调服务
- **Apache Curator 5.5.0**: ZooKeeper客户端库

### 3.2 开发环境
- **Java 17**: 开发语言
- **Maven 3.9.6**: 构建工具
- **Jackson 2.15.2**: JSON处理库

## 4. 消息协议

### 4.1 消息格式
```json
{
  "messageId": "uuid-timestamp-sequence",
  "type": "REQUEST|RESPONSE|REGISTER|HEARTBEAT|NOTIFY|AUTH",
  "source": "source-service-id",
  "target": "target-service-id",
  "timestamp": 1640995200000,
  "payload": "JSON string or null",
  "headers": {
    "key1": "value1",
    "key2": "value2"
  }
}
```

### 4.2 消息类型
| 类型 | 代码 | 描述 |
|------|------|------|
| REGISTER | 1 | 客户端注册消息 |
| AUTH | 6 | 认证消息 |
| HEARTBEAT | 2 | 心跳消息 |
| REQUEST | 3 | 请求消息（需要响应） |
| RESPONSE | 4 | 响应消息 |
| NOTIFY | 5 | 通知消息（无需响应） |

### 4.3 编解码器
- **JSON编解码器**: 使用Jackson进行消息序列化/反序列化
- **帧格式**: 长度前缀(4字节) + JSON数据
- **最大帧长度**: 10MB

## 5. 核心模块设计

### 5.1 公共模块 (common)
#### 5.1.1 Message类
- 消息实体定义
- JSON序列化/反序列化方法
- 消息ID生成器

#### 5.1.2 编解码器
- JsonMessageDecoder: Netty消息解码器
- JsonMessageEncoder: Netty消息编码器

### 5.2 路由节点模块 (router-node)
#### 5.2.1 Netty服务器
- 启动和停止Netty服务器
- 线程池配置（Boss/Worker线程）
- 连接参数配置

#### 5.2.2 处理器链
1. **LengthFieldBasedFrameDecoder**: 帧解码器
2. **LengthFieldPrepender**: 帧编码器
3. **JsonMessageDecoder**: JSON消息解码器
4. **JsonMessageEncoder**: JSON消息编码器
5. **AuthHandler**: 认证处理器
6. **HeartbeatHandler**: 心跳处理器
7. **MessageHandler**: 消息处理器

#### 5.2.3 连接管理
- ConnectionManager: 管理客户端连接
- 服务ID ↔ 通道映射
- 连接状态监控

#### 5.2.4 服务发现
- ServiceDiscovery: 从ZooKeeper发现服务
- 服务元数据管理

### 5.3 客户端节点模块 (client-node)
#### 5.3.1 Netty客户端
- 连接路由节点
- 自动重连机制
- 连接状态管理

#### 5.3.2 处理器链
1. **LengthFieldBasedFrameDecoder**: 帧解码器
2. **LengthFieldPrepender**: 帧编码器
3. **JsonMessageDecoder**: JSON消息解码器
4. **JsonMessageEncoder**: JSON消息编码器
5. **HeartbeatSender**: 心跳发送器
6. **ClientMessageHandler**: 客户端消息处理器

#### 5.3.3 重连管理器
- ReconnectionManager: 重连管理
- 指数退避算法
- 最大重试次数限制

#### 5.3.4 服务注册
- ServiceRegistry: 向ZooKeeper注册服务
- 服务元数据更新

#### 5.3.5 REST API
- MessageController: 消息发送API
- 支持请求/响应模式
- 支持通知模式

## 6. 交互流程

### 6.1 服务启动流程
```
1. 路由节点启动Netty服务器，监听8888端口
2. 客户端启动，连接ZooKeeper并注册服务
3. 客户端连接路由节点，发送AUTH消息
4. 路由节点验证令牌，返回认证结果
5. 客户端发送REGISTER消息注册服务
6. 路由节点记录连接信息，返回注册成功响应
```

### 6.2 消息传递流程
```
1. 客户端A构建REQUEST消息
2. 通过Netty连接发送到路由节点
3. 路由节点解析消息，查找目标服务连接
4. 转发消息到客户端B的Netty连接
5. 客户端B处理消息，发送RESPONSE消息
6. 路由节点将响应路由回客户端A
```

### 6.3 心跳机制
```
1. 客户端定期（20秒）发送HEARTBEAT消息
2. 路由节点收到心跳后立即回复
3. 路由节点检测读空闲（60秒），关闭无响应连接
4. 客户端检测写空闲，触发心跳发送
```

### 6.4 故障恢复流程
```
1. 连接断开检测（心跳超时/网络异常）
2. 客户端启动重连管理器
3. 使用指数退避算法尝试重连
4. 重连成功后重新认证和注册
5. ZooKeeper临时节点自动清理和重建
```

## 7. 配置说明

### 7.1 路由节点配置
```yaml
server:
  port: 8080

netty:
  server:
    port: 8888
    boss-threads: 1
    worker-threads: 8
    idle-timeout-seconds: 60

zookeeper:
  connect-string: localhost:2181
  session-timeout: 60000
```

### 7.2 客户端配置
```yaml
server:
  port: 8081

client:
  service-id: ${random.uuid}
  service-name: client-service
  auth-token: star-connection-token

netty:
  server:
    host: localhost
    port: 8888
    heartbeat-interval-seconds: 20

zookeeper:
  connect-string: localhost:2181
```

## 8. 安全性设计

### 8.1 认证机制
- 简单令牌认证
- 客户端连接时携带auth-token
- 认证失败关闭连接

### 8.2 消息验证
- 消息格式验证
- 来源服务验证
- 目标服务存在性检查

## 9. 性能考虑

### 9.1 连接管理
- 使用Netty的NIO模型
- 合理的线程池配置
- 连接资源及时释放

### 9.2 消息处理
- 异步消息处理
- 避免阻塞操作
- 消息大小限制（10MB）

### 9.3 资源管理
- 连接数限制
- 内存使用监控
- 线程池监控

## 10. 扩展性设计

### 10.1 水平扩展
- 路由节点可集群部署
- 客户端可动态增减
- ZooKeeper支持服务发现

### 10.2 协议扩展
- 支持JSON和Protobuf双协议
- 可扩展的消息类型
- 自定义消息头支持

### 10.3 功能扩展
- 消息持久化支持
- 消息确认机制
- 消息优先级支持

## 11. 监控与运维

### 11.1 健康检查
- Spring Boot Actuator集成
- 连接状态监控
- 服务注册状态监控

### 11.2 日志记录
- 详细的连接日志
- 消息流转日志
- 错误和异常日志

### 11.3 指标收集
- 连接数统计
- 消息吞吐量
- 响应时间统计

## 12. 部署方案

### 12.1 开发环境
```
1. 启动ZooKeeper单节点
2. 启动路由节点
3. 启动多个客户端节点
4. 通过REST API测试
```

### 12.2 生产环境
```
1. ZooKeeper集群（3节点）
2. 路由节点集群（负载均衡）
3. 客户端节点（按业务需求部署）
4. 监控和告警系统
```

## 13. 测试策略

### 13.1 单元测试
- 消息编解码测试
- 心跳机制测试
- 重连逻辑测试

### 13.2 集成测试
1. 启动测试环境
2. 连接测试
3. 消息路由测试
4. 故障恢复测试

### 13.3 性能测试
- 连接数压力测试
- 消息吞吐量测试
- 长时间运行稳定性测试

## 14. 已知限制

### 14.1 当前版本限制
- 单路由节点（可扩展为集群）
- 简单令牌认证
- 无消息持久化
- 无SSL/TLS加密

### 14.2 性能限制
- 单节点连接数受硬件限制
- 消息大小限制10MB
- 无消息压缩

## 15. 未来改进

### 15.1 短期改进
1. 添加Protobuf编解码器支持
2. 实现消息持久化和可靠投递
3. 添加监控和指标收集（Prometheus）

### 15.2 中期改进
1. 支持集群部署和负载均衡
2. 完善安全管理（SSL/TLS）
3. 添加消息确认和重试机制

### 15.3 长期改进
1. 支持多种网络协议（WebSocket）
2. 实现消息队列功能
3. 支持跨数据中心部署

## 16. 附录

### 16.1 依赖版本
| 组件 | 版本 | 说明 |
|------|------|------|
| Spring Boot | 3.1.5 | 微服务框架 |
| Netty | 4.1.100.Final | 网络通信框架 |
| ZooKeeper | 3.8.3 | 分布式协调服务 |
| Curator | 5.5.0 | ZooKeeper客户端 |
| Jackson | 2.15.2 | JSON处理库 |
| Java | 17 | 开发语言 |

### 16.2 端口分配
| 服务 | 端口 | 说明 |
|------|------|------|
| Router Node HTTP | 8080 | 管理接口 |
| Router Node Netty | 8888 | 消息通信 |
| Client Node 1 HTTP | 8081 | REST API |
| Client Node 2 HTTP | 8082 | REST API |
| ZooKeeper | 2181 | 服务注册 |

### 16.3 关键配置项
| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| netty.server.port | 8888 | Netty服务器端口 |
| netty.server.idle-timeout-seconds | 60 | 连接空闲超时 |
| netty.server.heartbeat-interval-seconds | 20 | 心跳间隔 |
| zookeeper.connect-string | localhost:2181 | ZooKeeper连接 |
| zookeeper.session-timeout | 60000 | 会话超时(ms) |

---

**文档版本**: 1.0
**最后更新**: 2026-03-04
**作者**: Claude Code Assistant
**状态**: 审核通过