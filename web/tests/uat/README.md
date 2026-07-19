# SmartCS UAT 自动化测试

基于 Playwright 的用户验收测试，模拟真实用户操作流程。

## 环境要求

- Node.js 18+
- 服务运行中: Bot (8000), Assist (8001), star-conn CF (8080), Agent Workbench (5173)
- 至少 1 名坐席在线

## 安装

```bash
cd web/tests/uat
npm init -y
npm install @playwright/test
npx playwright install chromium
```

## 运行

```bash
# 运行单个场景
npx playwright test scenario_a_bot_self_service.spec.ts

# 运行所有场景
npx playwright test

# 带 UI 模式 (调试)
npx playwright test --ui

# 生成 HTML 报告
npx playwright test --reporter=html
npx playwright show-report
```

## 测试场景

| 文件 | 场景 | 说明 |
|------|------|------|
| scenario_a_bot_self_service.spec.ts | 场景 A | 客户自助咨询 |
| scenario_b_transfer_to_agent.spec.ts | 场景 B | 转人工 + 坐席辅助 |
| scenario_c_compliance_alert.spec.ts | 场景 C | 合规风险告警 |

## 前置条件

1. 所有中间件已启动 (docker compose up -d)
2. Bot (:8000)、Assist (:8001) 已启动
3. star-conn CF (:8080)、AB (:8081) 已启动
4. Agent Workbench (:5173) 已启动
5. 至少 1 名坐席已登录

## data-testid 约定

前端组件需添加 `data-testid` 属性以便测试脚本定位元素：

| data-testid | 说明 |
|-------------|------|
| chat-input | 聊天输入框 |
| send-button | 发送按钮 |
| message-bot | Bot 消息气泡 |
| message-customer | 客户消息气泡 |
| message-agent | 坐席消息气泡 |
| session-list | 会话列表 |
| accept-session-button | 接听按钮 |
| assist-panel | 辅助推送面板 |
| script-card | 话术卡片 |
| alert-banner | 告警横幅 |
| risk-badge | 风险徽章 |
| risk-block-card | 风控拦截卡片 |
| marketing-card | 营销推荐卡片 |
