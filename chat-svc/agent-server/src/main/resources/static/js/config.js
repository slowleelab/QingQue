/**
 * 坐席工作台配置文件
 *
 * 部署说明:
 * 1. 前后端一体部署: 保持默认配置，使用相对路径
 * 2. 前后端分离部署: 修改 API_BASE_URL 和 WS_BASE_URL 为后端服务地址
 *
 * 示例配置:
 * - 一体部署: API_BASE_URL = '/api/agent', WS_BASE_URL = null (自动检测)
 * - 分离部署: API_BASE_URL = 'https://api.example.com/api/agent',
 *             WS_BASE_URL = 'wss://api.example.com/ws/agent'
 */

const AGENT_CONFIG = {
    // API 基础地址
    // 前后端一体: '/api/agent'
    // 前后端分离: 'https://api.example.com/api/agent'
    API_BASE_URL: '/api/agent',

    // WebSocket 基础地址
    // null 表示自动根据当前页面地址生成
    // 前后端一体: null (自动: ws(s)://当前host/ws/agent)
    // 前后端分离: 'wss://api.example.com/ws/agent'
    WS_BASE_URL: null,

    // 重连间隔 (毫秒)
    RECONNECT_INTERVAL: 5000,

    // 心跳间隔 (毫秒)
    HEARTBEAT_INTERVAL: 30000,

    // 通知显示时长 (毫秒)
    NOTIFICATION_DURATION: 5000
};

// 导出配置 (兼容模块化和全局变量)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = AGENT_CONFIG;
}
