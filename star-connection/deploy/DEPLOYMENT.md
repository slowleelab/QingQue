# 坐席工作台前后端分离部署指南

本文档说明如何将坐席工作台前端与后端分离部署。

## 架构说明

### 一体部署架构 (当前默认)

```
┌─────────────────────────────────────────┐
│         Agent Backend (jar)              │
│              端口: 8081                  │
│  ┌─────────────────────────────────────┐│
│  │  Spring Boot + 内嵌 Tomcat           ││
│  │  ┌─────────────┐  ┌───────────────┐  ││
│  │  │ 静态资源     │  │ WebSocket     │  ││
│  │  │ /static/*   │  │ /ws/agent/*   │  ││
│  │  └─────────────┘  └───────────────┘  ││
│  └─────────────────────────────────────┘│
└─────────────────────────────────────────┘
```

### 分离部署架构 (推荐生产环境)

```
┌──────────────────────────────────────────────────────────────┐
│                         Nginx                                 │
│                     端口: 80/443                              │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  静态资源服务                API 反向代理                 │ │
│  │  location / {              location /api/ {              │ │
│  │      root /var/www/agent;      proxy_pass backend;       │ │
│  │  }                          }                            │ │
│  │                           location /ws/agent {           │ │
│  │                               proxy_pass backend;         │ │
│  │                               WebSocket 升级配置...       │ │
│  │                           }                              │ │
│  └─────────────────────────────────────────────────────────┘ │
│                           │                                   │
│  /var/www/agent/          │                                   │
│  ├── index.html           │                                   │
│  ├── js/                  │                                   │
│  │   ├── config.js        │                                   │
│  │   └── agent.js         │                                   │
│  └── css/                 │                                   │
└───────────────────────────┼──────────────────────────────────┘
                            │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
   │  Backend-1  │  │  Backend-2  │  │  Backend-3  │
   │   :8081     │  │   :8082     │  │   :8083     │
   │  (纯 API)   │  │  (纯 API)   │  │  (纯 API)   │
   └─────────────┘  └─────────────┘  └─────────────┘
```

## 部署步骤

### 步骤 1: 构建后端服务

```bash
# 进入项目根目录
cd star-connection

# 构建所有模块
mvn clean package -DskipTests

# 后端 jar 包位置
# agent-backend/target/agent-backend-1.0.0.jar
```

### 步骤 2: 提取前端静态文件

从 `agent-backend/src/main/resources/static/` 目录复制以下文件到 nginx 静态资源目录：

```bash
# 创建目标目录
sudo mkdir -p /var/www/agent-frontend

# 复制静态文件
sudo cp -r agent-backend/src/main/resources/static/* /var/www/agent-frontend/

# 目录结构
# /var/www/agent-frontend/
# ├── index.html
# ├── js/
# │   ├── config.js
# │   └── agent.js
# ├── css/
# │   └── agent.css
# └── sounds/
#     └── notification.mp3
```

### 步骤 3: 修改前端配置

编辑 `/var/www/agent-frontend/js/config.js`，配置后端 API 地址：

```javascript
const AGENT_CONFIG = {
    // API 基础地址 - 修改为实际的后端服务地址
    API_BASE_URL: 'https://api.example.com/api/agent',

    // WebSocket 基础地址 - 修改为实际的后端 WebSocket 地址
    WS_BASE_URL: 'wss://api.example.com/ws/agent',

    // 其他配置保持默认
    RECONNECT_INTERVAL: 5000,
    HEARTBEAT_INTERVAL: 30000,
    NOTIFICATION_DURATION: 5000
};
```

### 步骤 4: 配置 Nginx

将 `deploy/nginx/agent-frontend.conf` 复制到 nginx 配置目录：

```bash
# 复制配置文件
sudo cp deploy/nginx/agent-frontend.conf /etc/nginx/conf.d/

# 修改配置文件中的以下内容：
# 1. server_name: 修改为实际域名
# 2. root: 修改为前端静态文件实际路径
# 3. upstream backend_cluster: 修改为实际后端服务地址
```

编辑 `/etc/nginx/conf.d/agent-frontend.conf`：

```nginx
# 修改后端服务地址
upstream backend_cluster {
    least_conn;
    server 192.168.1.101:8081 weight=1;
    server 192.168.1.102:8081 weight=1;
    # 添加更多后端节点...
}

server {
    listen 80;
    server_name your-domain.com;  # 修改为实际域名

    # 修改前端静态文件路径
    root /var/www/agent-frontend;

    # 其他配置保持不变...
}
```

### 步骤 5: 配置后端 CORS

编辑 `agent-backend/src/main/resources/application.yml`，配置允许的前端域名：

```yaml
# CORS 跨域配置（前后端分离部署时使用）
cors:
  # 允许的前端域名，多个用逗号分隔
  # 开发环境
  allowed-origins: "http://localhost:3000,http://127.0.0.1:3000"

  # 生产环境示例
  # allowed-origins: "https://agent.example.com"
```

**重要**: 生产环境请将 `allowed-origins` 设置为前端的实际域名，不要使用 `*`。

### 步骤 6: 启动服务

```bash
# 启动后端服务 (多节点)
java -jar agent-backend/target/agent-backend-1.0.0.jar --server.port=8081 &
java -jar agent-backend/target/agent-backend-1.0.0.jar --server.port=8082 --agent-backend.service-id=backend-2 &

# 测试 nginx 配置
sudo nginx -t

# 重载 nginx
sudo nginx -s reload
```

### 步骤 7: 验证部署

1. **访问前端页面**: `http://your-domain.com/`
2. **检查 API 连接**: 在浏览器控制台查看网络请求
3. **检查 WebSocket 连接**: 登录后查看 WebSocket 连接状态

## 配置详解

### 前端配置 (config.js)

| 配置项 | 说明 | 一体部署 | 分离部署 |
|--------|------|----------|----------|
| `API_BASE_URL` | API 基础地址 | `/api/agent` | `https://api.example.com/api/agent` |
| `WS_BASE_URL` | WebSocket 地址 | `null` (自动检测) | `wss://api.example.com/ws/agent` |
| `RECONNECT_INTERVAL` | 重连间隔 (毫秒) | 5000 | 5000 |
| `HEARTBEAT_INTERVAL` | 心跳间隔 (毫秒) | 30000 | 30000 |

### 后端 CORS 配置 (application.yml)

```yaml
cors:
  # 一体部署: 使用 * 允许所有来源
  allowed-origins: "*"

  # 分离部署: 指定前端域名
  # allowed-origins: "https://agent.example.com,http://localhost:3000"
```

### Nginx 配置要点

#### 静态资源缓存

```nginx
# JS/CSS/图片等静态资源缓存 7 天
location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
    expires 7d;
    add_header Cache-Control "public, immutable";
}

# HTML 文件不缓存，确保更新后用户获取最新版本
location ~* \.html$ {
    add_header Cache-Control "no-cache, no-store, must-revalidate";
}
```

#### WebSocket 代理

```nginx
location /ws/agent {
    proxy_pass http://backend_cluster/ws/agent;
    proxy_http_version 1.1;

    # WebSocket 升级头 - 必需
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";

    # 长连接超时配置
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;

    # 禁用缓冲
    proxy_buffering off;
}
```

## HTTPS 配置 (生产环境推荐)

```nginx
server {
    listen 443 ssl http2;
    server_name agent.example.com;

    # SSL 证书
    ssl_certificate /etc/nginx/ssl/agent.example.com.crt;
    ssl_certificate_key /etc/nginx/ssl/agent.example.com.key;

    # SSL 配置
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;

    # HSTS
    add_header Strict-Transport-Security "max-age=31536000" always;

    # 其他配置同 HTTP...
}

# HTTP 重定向到 HTTPS
server {
    listen 80;
    server_name agent.example.com;
    return 301 https://$server_name$request_uri;
}
```

前端配置也需要相应修改为 HTTPS 地址：

```javascript
const AGENT_CONFIG = {
    API_BASE_URL: 'https://api.example.com/api/agent',
    WS_BASE_URL: 'wss://api.example.com/ws/agent',
    // ...
};
```

## 故障排查

### 1. CORS 错误

**症状**: 浏览器控制台显示 CORS 相关错误

**解决方案**:
1. 检查后端 `application.yml` 中 `cors.allowed-origins` 配置
2. 确保配置了正确的前端域名
3. 重启后端服务

### 2. WebSocket 连接失败

**症状**: WebSocket 连接被拒绝或断开

**解决方案**:
1. 检查 nginx WebSocket 代理配置
2. 确保配置了 `Upgrade` 和 `Connection` 头
3. 检查后端 WebSocket 配置 `websocket.agent.enabled: true`
4. 检查后端 CORS 配置是否包含前端域名

### 3. 静态资源 404

**症状**: 前端页面无法加载 JS/CSS 文件

**解决方案**:
1. 检查 nginx `root` 配置是否正确
2. 确保静态文件已正确复制到目标目录
3. 检查文件权限

### 4. API 请求 502/504

**症状**: API 请求返回 502 或 504 错误

**解决方案**:
1. 检查后端服务是否正常运行
2. 检查 nginx `upstream` 配置是否正确
3. 检查后端服务端口是否可达
4. 查看后端日志排查错误

## 开发环境配置

开发环境可以使用代理服务器解决跨域问题：

### 使用 Vue CLI 开发服务器代理

```javascript
// vue.config.js
module.exports = {
    devServer: {
        proxy: {
            '/api': {
                target: 'http://localhost:8081',
                changeOrigin: true
            },
            '/ws': {
                target: 'ws://localhost:8081',
                ws: true
            }
        }
    }
}
```

### 使用 Vite 开发服务器代理

```javascript
// vite.config.js
export default {
    server: {
        proxy: {
            '/api': {
                target: 'http://localhost:8081',
                changeOrigin: true
            },
            '/ws': {
                target: 'ws://localhost:8081',
                ws: true
            }
        }
    }
}
```

## 回滚到一体部署

如需回滚到一体部署模式：

1. 将 `config.js` 中的配置恢复为默认值：
```javascript
const AGENT_CONFIG = {
    API_BASE_URL: '/api/agent',
    WS_BASE_URL: null,
    // ...
};
```

2. 后端 `application.yml` 中 CORS 配置设置为 `*`：
```yaml
cors:
  allowed-origins: "*"
```

3. 直接访问后端服务：`http://localhost:8081/`
