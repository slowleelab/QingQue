/**
 * 在线客服系统监控页面
 * Customer Service Platform - Command Center Dashboard
 * Enterprise-grade monitoring interface with refined aesthetics
 */

const API_BASE = '/api/monitor';
const AUTO_REFRESH_INTERVAL = 5000;
const RECENT_SESSIONS_LIMIT = 10;

let autoRefreshTimer = null;
let customerServiceStats = null;
let sessions = [];
let agents = [];
let services = [];
let connections = [];
let frontendNodes = [];
let backendNodes = [];

// 分页状态
let currentPage = 0;
let pageSize = 10;
let totalElements = 0;
let currentFilters = {
    sessionId: '',
    customerId: '',
    agentId: '',
    status: '',
    startTime: null,
    endTime: null
};

// DOM 元素缓存
const elements = {
    // 状态
    lastUpdateTime: document.getElementById('lastUpdateTime'),
    refreshBtn: document.getElementById('refreshBtn'),
    systemStatus: document.getElementById('systemStatus'),
    zkStatus: document.getElementById('zkStatus'),

    // 会话统计
    waitingSessions: document.getElementById('waitingSessions'),
    activeSessions: document.getElementById('activeSessions'),
    totalSessions: document.getElementById('totalSessions'),

    // 坐席统计
    onlineAgents: document.getElementById('onlineAgents'),
    busyAgents: document.getElementById('busyAgents'),
    offlineAgents: document.getElementById('offlineAgents'),
    agentDots: document.getElementById('agentDots'),

    // 连接
    totalConnections: document.getElementById('totalConnections'),
    webSocketConnections: document.getElementById('webSocketConnections'),

    // 会话列表
    sessionCount: document.getElementById('sessionCount'),
    sessionCards: document.getElementById('sessionCards'),

    // 坐席列表
    agentCount: document.getElementById('agentCount'),
    agentCards: document.getElementById('agentCards'),

    // 前端节点
    frontendCount: document.getElementById('frontendCount'),
    frontendCards: document.getElementById('frontendCards'),

    // 后台节点
    backendCount: document.getElementById('backendCount'),
    backendCards: document.getElementById('backendCards'),

    // 拓扑图
    topologySvg: document.getElementById('topologySvg'),
    topologyContainer: document.getElementById('topologyContainer'),

    // 分页
    pagination: document.getElementById('pagination'),
    prevPage: document.getElementById('prevPage'),
    nextPage: document.getElementById('nextPage'),
    pageInfo: document.getElementById('pageInfo'),

    // 过滤器
    filterSessionId: document.getElementById('filterSessionId'),
    filterCustomerId: document.getElementById('filterCustomerId'),
    filterStatus: document.getElementById('filterStatus'),
    filterStartTime: document.getElementById('filterStartTime'),
    filterEndTime: document.getElementById('filterEndTime'),
    searchBtn: document.getElementById('searchBtn'),
    resetBtn: document.getElementById('resetBtn'),

    // 弹窗
    sessionModal: document.getElementById('sessionModal'),
    closeModal: document.getElementById('closeModal'),
    sessionDetail: document.getElementById('sessionDetail'),

    // 节点弹窗
    nodeModal: document.getElementById('nodeModal'),
    closeNodeModal: document.getElementById('closeNodeModal'),
    nodeDetail: document.getElementById('nodeDetail'),

    // ZooKeeper弹窗
    zkModal: document.getElementById('zkModal'),
    closeZkModal: document.getElementById('closeZkModal'),
    zkDetail: document.getElementById('zkDetail')
};

/**
 * 格式化时间
 */
function formatTime(timestamp) {
    if (!timestamp) return '--:--:--';
    const date = new Date(timestamp);
    return date.toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

/**
 * 格式化日期时间
 */
function formatDateTime(timestamp) {
    if (!timestamp) return '--';
    const date = new Date(timestamp);
    return date.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

/**
 * 格式化持续时间
 * @param {number} startTimeOrMillis - 开始时间戳(毫秒) 或 已运行的毫秒数
 * @param {boolean} isMillis - 如果为 true，表示第一个参数是已运行的毫秒数
 */
function formatDuration(startTimeOrMillis, isMillis = false) {
    if (!startTimeOrMillis) return '--';
    let diff;
    if (isMillis) {
        diff = startTimeOrMillis;
    } else {
        diff = Date.now() - startTimeOrMillis;
    }
    const seconds = Math.floor(diff / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);

    if (hours > 0) {
        return `${hours}小时${minutes % 60}分`;
    } else if (minutes > 0) {
        return `${minutes}分${seconds % 60}秒`;
    } else {
        return `${seconds}秒`;
    }
}

/**
 * 获取客服系统统计
 */
async function fetchCustomerServiceStats() {
    try {
        const response = await fetch(`${API_BASE}/customer-service/stats`);
        if (!response.ok) throw new Error('获取客服统计失败');
        return await response.json();
    } catch (error) {
        console.error('获取客服统计失败:', error);
        return null;
    }
}

/**
 * 获取活跃会话列表
 */
async function fetchActiveSessions() {
    try {
        const response = await fetch(`${API_BASE}/customer-service/sessions`);
        if (!response.ok) throw new Error('获取会话列表失败');
        return await response.json();
    } catch (error) {
        console.error('获取会话列表失败:', error);
        return [];
    }
}

/**
 * 查询会话列表
 */
async function querySessions() {
    try {
        const params = new URLSearchParams();
        if (currentFilters.sessionId) params.append('sessionId', currentFilters.sessionId);
        if (currentFilters.customerId) params.append('customerId', currentFilters.customerId);
        if (currentFilters.agentId) params.append('agentId', currentFilters.agentId);
        if (currentFilters.status) params.append('status', currentFilters.status);
        if (currentFilters.startTime) params.append('startTime', currentFilters.startTime);
        if (currentFilters.endTime) params.append('endTime', currentFilters.endTime);
        params.append('page', currentPage);
        params.append('size', pageSize);

        const response = await fetch(`${API_BASE}/customer-service/sessions/query?${params.toString()}`);
        if (!response.ok) throw new Error('查询会话失败');
        return await response.json();
    } catch (error) {
        console.error('查询会话失败:', error);
        return { content: [], totalElements: 0, page: 0, size: 10 };
    }
}

/**
 * 获取会话详情
 */
async function fetchSessionDetail(sessionId) {
    try {
        const response = await fetch(`${API_BASE}/customer-service/sessions/${sessionId}`);
        if (!response.ok) {
            if (response.status === 404) return null;
            throw new Error('获取会话详情失败');
        }
        return await response.json();
    } catch (error) {
        console.error('获取会话详情失败:', error);
        return null;
    }
}

/**
 * 获取最近会话
 */
async function fetchRecentSessions() {
    try {
        const response = await fetch(`${API_BASE}/customer-service/sessions/recent?limit=${RECENT_SESSIONS_LIMIT}`);
        if (!response.ok) throw new Error('获取最近会话失败');
        return await response.json();
    } catch (error) {
        console.error('获取最近会话失败:', error);
        return [];
    }
}

/**
 * 获取坐席列表
 */
async function fetchAgents() {
    try {
        const response = await fetch(`${API_BASE}/customer-service/agents`);
        if (!response.ok) throw new Error('获取坐席列表失败');
        return await response.json();
    } catch (error) {
        console.error('获取坐席列表失败:', error);
        return [];
    }
}

/**
 * 获取健康检查
 */
async function fetchHealth() {
    try {
        const response = await fetch(`${API_BASE}/health`);
        if (!response.ok) throw new Error('获取健康状态失败');
        return await response.json();
    } catch (error) {
        console.error('获取健康状态失败:', error);
        return null;
    }
}

/**
 * 获取连接详情
 */
async function fetchConnections() {
    try {
        const response = await fetch(`${API_BASE}/connections`);
        if (!response.ok) throw new Error('获取连接失败');
        return await response.json();
    } catch (error) {
        console.error('获取连接失败:', error);
        return [];
    }
}

/**
 * 获取服务列表
 */
async function fetchServices() {
    try {
        const response = await fetch(`${API_BASE}/services`);
        if (!response.ok) throw new Error('获取服务失败');
        return await response.json();
    } catch (error) {
        console.error('获取服务失败:', error);
        return [];
    }
}

/**
 * 获取所有节点指标
 */
async function fetchNodeMetrics() {
    try {
        const response = await fetch(`${API_BASE}/nodes/metrics`);
        if (!response.ok) throw new Error('获取节点指标失败');
        return await response.json();
    } catch (error) {
        console.error('获取节点指标失败:', error);
        return [];
    }
}

/**
 * 获取ZooKeeper元数据
 */
async function fetchZookeeperMetadata() {
    try {
        const response = await fetch(`${API_BASE}/zookeeper/metadata`);
        if (!response.ok) throw new Error('获取ZooKeeper元数据失败');
        return await response.json();
    } catch (error) {
        console.error('获取ZooKeeper元数据失败:', error);
        return null;
    }
}

/**
 * 更新统计面板
 */
function updateStats(stats, connectionsData, servicesData, healthData, frontendsData, backendsData) {
    if (!stats) return;

    // 会话统计
    const sessionStats = stats.session || {};
    elements.waitingSessions.textContent = sessionStats.waiting || 0;
    elements.activeSessions.textContent = sessionStats.active || 0;
    elements.totalSessions.textContent = sessionStats.total || 0;

    // 坐席统计
    const agentStats = stats.agent || {};
    elements.onlineAgents.textContent = agentStats.online || 0;
    elements.busyAgents.textContent = agentStats.busy || 0;
    elements.offlineAgents.textContent = agentStats.offline || 0;
    updateAgentDots(agentStats);

    // Netty 连接数 = 所有前端节点的连接总和
    const totalNettyConnections = (frontendsData || []).reduce((sum, node) => sum + (node.nettyConnections || 0), 0);
    elements.totalConnections.textContent = totalNettyConnections;

    // WebSocket 连接数 = 客户连接数 + 所有后台节点的坐席数
    const customerWsCount = healthData && healthData.webSocketConnections !== undefined ? healthData.webSocketConnections : 0;
    const agentWsCount = (backendsData || []).reduce((sum, node) => sum + (node.agentCount || 0), 0);
    const totalWsCount = customerWsCount + agentWsCount;
    elements.webSocketConnections.textContent = totalWsCount;
}

/**
 * 更新坐席状态点
 */
function updateAgentDots(agentStats) {
    const total = agentStats.total || 0;
    const online = agentStats.online || 0;
    const busy = agentStats.busy || 0;

    let html = '';
    for (let i = 0; i < total; i++) {
        let statusClass = 'offline';
        if (i < online) {
            statusClass = 'online';
        } else if (i < online + busy) {
            statusClass = 'busy';
        }
        html += `<span class="stat-dot ${statusClass}"></span>`;
    }
    elements.agentDots.innerHTML = html;
}

/**
 * 更新会话卡片
 */
function updateSessionCards(sessionsData, total) {
    elements.sessionCount.textContent = `${total || sessionsData.length} 个会话`;

    if (sessionsData.length === 0) {
        elements.sessionCards.innerHTML = '<div class="no-data">暂无会话</div>';
        return;
    }

    let html = '';
    sessionsData.forEach(session => {
        const statusClass = session.status === 'WAITING' ? 'waiting' :
                           (session.status === 'ACTIVE' ? 'active' : 'closed');
        const statusText = {
            'WAITING': '等待中',
            'ACTIVE': '进行中',
            'CLOSED': '已关闭'
        }[session.status] || session.status;
        const duration = formatDuration(session.createTime);

        html += `
            <div class="session-card ${statusClass}" data-session-id="${session.sessionId}">
                <div class="card-header">
                    <div class="card-title">
                        <div class="card-icon session-icon">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                            </svg>
                        </div>
                        <div>
                            <div class="card-name">${session.sessionId || '--'}</div>
                            <div class="card-subname">${session.customerName || session.customerId || '访客'}</div>
                        </div>
                    </div>
                    <div class="card-status ${statusClass}">
                        <span class="card-status-dot"></span>
                        ${statusText}
                    </div>
                </div>
                <div class="card-body">
                    <div class="card-info">
                        <span class="card-info-label">坐席</span>
                        <span class="card-info-value">${session.agentId || '未分配'}</span>
                    </div>
                    <div class="card-info">
                        <span class="card-info-label">时长</span>
                        <span class="card-info-value">${duration}</span>
                    </div>
                    <div class="card-info">
                        <span class="card-info-label">后台</span>
                        <span class="card-info-value">${session.backendId || '--'}</span>
                    </div>
                </div>
            </div>
        `;
    });

    elements.sessionCards.innerHTML = html;

    // 绑定点击事件
    document.querySelectorAll('.session-card').forEach(card => {
        card.addEventListener('click', () => showSessionDetail(card.dataset.sessionId));
    });
}

/**
 * 更新分页
 */
function updatePagination() {
    const totalPages = Math.ceil(totalElements / pageSize);
    elements.pageInfo.textContent = `第 ${currentPage + 1} 页 / 共 ${totalPages || 1} 页 (${totalElements} 条)`;
    elements.prevPage.disabled = currentPage <= 0;
    elements.nextPage.disabled = currentPage >= totalPages - 1;
}

/**
 * 显示会话详情
 */
async function showSessionDetail(sessionId) {
    const session = await fetchSessionDetail(sessionId);
    if (!session) {
        alert('会话不存在');
        return;
    }

    const statusText = {
        'WAITING': '等待中',
        'ACTIVE': '进行中',
        'CLOSED': '已关闭'
    }[session.status] || session.status;

    const statusClass = session.status === 'WAITING' ? 'waiting' :
                       (session.status === 'ACTIVE' ? 'active' : 'closed');

    elements.sessionDetail.innerHTML = `
        <div class="detail-row">
            <span class="detail-label">会话ID</span>
            <span class="detail-value">${session.sessionId}</span>
        </div>
        <div class="detail-row">
            <span class="detail-label">客户ID</span>
            <span class="detail-value">${session.customerId || '--'}</span>
        </div>
        <div class="detail-row">
            <span class="detail-label">客户名称</span>
            <span class="detail-value">${session.customerName || '访客'}</span>
        </div>
        <div class="detail-row">
            <span class="detail-label">坐席ID</span>
            <span class="detail-value">${session.agentId || '未分配'}</span>
        </div>
        <div class="detail-row">
            <span class="detail-label">后台节点</span>
            <span class="detail-value">${session.backendId || '--'}</span>
        </div>
        <div class="detail-row">
            <span class="detail-label">状态</span>
            <span class="detail-value"><span class="card-status ${statusClass}">${statusText}</span></span>
        </div>
        <div class="detail-row">
            <span class="detail-label">创建时间</span>
            <span class="detail-value">${formatDateTime(session.createTime)}</span>
        </div>
        <div class="detail-row">
            <span class="detail-label">更新时间</span>
            <span class="detail-value">${formatDateTime(session.updateTime)}</span>
        </div>
        <div class="detail-row">
            <span class="detail-label">持续时长</span>
            <span class="detail-value">${formatDuration(session.createTime)}</span>
        </div>
    `;

    elements.sessionModal.classList.add('visible');
}

/**
 * 显示节点指标
 */
async function showNodeMetrics(nodeId, nodeType) {
    // 如果是 CONNECTION 类型，从 connections 中获取数据
    if (nodeType === 'CONNECTION') {
        const conn = connections.find(c => (c.serviceId || c.channelId) === nodeId);
        if (!conn) {
            elements.nodeDetail.innerHTML = '<div class="no-data">无法获取连接详情</div>';
            elements.nodeModal.classList.add('visible');
            return;
        }

        const statusClass = conn.status === 'ACTIVE' ? 'connected' : 'disconnected';
        const connTime = conn.connectedSince ? formatDuration(conn.connectedSince) : '--';

        elements.nodeDetail.innerHTML = `
            <div class="detail-row">
                <span class="detail-label">连接ID</span>
                <span class="detail-value">${conn.serviceId || 'Unknown'}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">Channel ID</span>
                <span class="detail-value" style="font-size: 0.85rem; font-family: monospace;">${conn.channelId || '--'}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">连接类型</span>
                <span class="detail-value">WebSocket</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">状态</span>
                <span class="detail-value"><span class="card-status ${statusClass}">${conn.status || 'UNKNOWN'}</span></span>
            </div>
            <div class="detail-row">
                <span class="detail-label">远程地址</span>
                <span class="detail-value">${conn.remoteAddress || 'N/A'}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">认证状态</span>
                <span class="detail-value">${conn.authenticated ? '已认证' : '未认证'}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">连接时长</span>
                <span class="detail-value">${connTime}</span>
            </div>
        `;

        elements.nodeModal.classList.add('visible');
        return;
    }

    // Router 节点
    const metrics = await fetchNodeMetrics();
    const nodeMetric = metrics.find(m => m.nodeId === nodeId);

    if (!nodeMetric) {
        elements.nodeDetail.innerHTML = '<div class="no-data">无法获取节点指标</div>';
        elements.nodeModal.classList.add('visible');
        return;
    }

    const formatBytes = (bytes) => {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    };

    const formatUptime = (millis) => {
        if (!millis) return '--';
        const seconds = Math.floor(millis / 1000);
        const minutes = Math.floor(seconds / 60);
        const hours = Math.floor(minutes / 60);
        const days = Math.floor(hours / 24);

        if (days > 0) return `${days}天 ${hours % 24}小时`;
        if (hours > 0) return `${hours}小时 ${minutes % 60}分`;
        if (minutes > 0) return `${minutes}分 ${seconds % 60}秒`;
        return `${seconds}秒`;
    };

    const memoryClass = nodeMetric.memoryUsagePercent > 80 ? 'danger' :
                        nodeMetric.memoryUsagePercent > 60 ? 'warning' : 'normal';
    const cpuClass = nodeMetric.cpuUsage > 80 ? 'danger' :
                     nodeMetric.cpuUsage > 60 ? 'warning' : 'normal';

    elements.nodeDetail.innerHTML = `
        <div class="detail-row">
            <span class="detail-label">节点ID</span>
            <span class="detail-value">${nodeMetric.nodeId}</span>
        </div>
        <div class="detail-row">
            <span class="detail-label">节点类型</span>
            <span class="detail-value">${nodeMetric.nodeType}</span>
        </div>
        <div class="detail-row">
            <span class="detail-label">地址</span>
            <span class="detail-value">${nodeMetric.address}:${nodeMetric.port}</span>
        </div>
        <div class="detail-row">
            <span class="detail-label">状态</span>
            <span class="detail-value"><span class="card-status ${nodeMetric.status === 'RUNNING' || nodeMetric.status === 'ACTIVE' ? 'connected' : 'disconnected'}">${nodeMetric.status}</span></span>
        </div>
        <div class="detail-row">
            <span class="detail-label">启动时间</span>
            <span class="detail-value">${nodeMetric.startTime || '--'}</span>
        </div>
        <div class="detail-row">
            <span class="detail-label">运行时长</span>
            <span class="detail-value">${formatUptime(nodeMetric.uptimeMillis)}</span>
        </div>

        <div class="detail-section">
            <div class="detail-section-title">JVM 指标</div>
            <div class="metrics-grid">
                <div class="metric-card">
                    <div class="metric-header">
                        <span class="metric-label">CPU 使用率</span>
                        <span class="metric-badge ${cpuClass}">${nodeMetric.cpuUsage > 80 ? '高' : nodeMetric.cpuUsage > 60 ? '中' : '正常'}</span>
                    </div>
                    <div class="metric-value">${nodeMetric.cpuUsage || 0}<span class="metric-unit">%</span></div>
                    <div class="metric-bar">
                        <div class="metric-bar-fill ${cpuClass}" style="width: ${Math.min(nodeMetric.cpuUsage || 0, 100)}%"></div>
                    </div>
                </div>
                <div class="metric-card">
                    <div class="metric-header">
                        <span class="metric-label">内存使用</span>
                        <span class="metric-badge ${memoryClass}">${nodeMetric.memoryUsagePercent > 80 ? '高' : nodeMetric.memoryUsagePercent > 60 ? '中' : '正常'}</span>
                    </div>
                    <div class="metric-value">${nodeMetric.memoryUsagePercent || 0}<span class="metric-unit">%</span></div>
                    <div class="metric-bar">
                        <div class="metric-bar-fill ${memoryClass}" style="width: ${Math.min(nodeMetric.memoryUsagePercent || 0, 100)}%"></div>
                    </div>
                    <div style="font-size: 0.7rem; color: var(--text-tertiary); margin-top: 4px;">
                        ${formatBytes(nodeMetric.memoryUsed)} / ${formatBytes(nodeMetric.memoryMax)}
                    </div>
                </div>
                <div class="metric-card">
                    <div class="metric-header">
                        <span class="metric-label">活跃线程</span>
                    </div>
                    <div class="metric-value">${nodeMetric.threadCount || 0}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-header">
                        <span class="metric-label">峰值线程</span>
                    </div>
                    <div class="metric-value">${nodeMetric.peakThreadCount || 0}</div>
                </div>
            </div>
        </div>

        <div class="detail-section">
            <div class="detail-section-title">连接指标</div>
            <div class="metrics-grid">
                <div class="metric-card">
                    <div class="metric-header">
                        <span class="metric-label">活跃连接</span>
                    </div>
                    <div class="metric-value">${nodeMetric.activeConnections || 0}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-header">
                        <span class="metric-label">总连接数</span>
                    </div>
                    <div class="metric-value">${nodeMetric.totalConnections || 0}</div>
                </div>
            </div>
        </div>
    `;

    elements.nodeModal.classList.add('visible');
}

/**
 * 显示ZooKeeper元数据
 */
async function showZookeeperMetadata() {
    const zkData = await fetchZookeeperMetadata();

    if (!zkData) {
        elements.zkDetail.innerHTML = '<div class="no-data">无法获取ZooKeeper元数据</div>';
        elements.zkModal.classList.add('visible');
        return;
    }

    let html = `
        <div class="zk-status">
            <div class="zk-status-indicator ${zkData.connected ? 'connected' : 'disconnected'}"></div>
            <div class="zk-info">
                <div class="zk-info-label">连接状态</div>
                <div class="zk-info-value">${zkData.connected ? '已连接' : '未连接'}</div>
            </div>
        </div>
        <div class="detail-row">
            <span class="detail-label">连接地址</span>
            <span class="detail-value">${zkData.connectString || 'N/A'}</span>
        </div>
    `;

    if (zkData.services && zkData.services.length > 0) {
        html += `
            <div class="zk-services">
                <div class="zk-services-title">已注册服务 (${zkData.services.length})</div>
        `;

        zkData.services.forEach(service => {
            const serviceType = service.metadata && service.metadata['service-type'] || 'unknown';
            html += `
                <div class="zk-service-card">
                    <div class="zk-service-header">
                        <span class="zk-service-name">${service.serviceId}</span>
                        <span class="zk-service-type">${serviceType}</span>
                    </div>
                    <div class="zk-service-meta">
                        <span>名称: ${service.serviceName}</span>
                        <span>地址: ${service.address}:${service.port}</span>
                    </div>
                </div>
            `;
        });

        html += '</div>';
    } else {
        html += `
            <div class="detail-section">
                <div class="no-data">暂无已注册服务</div>
            </div>
        `;
    }

    elements.zkDetail.innerHTML = html;
    elements.zkModal.classList.add('visible');
}

/**
 * 更新坐席卡片
 */
function updateAgentCards(agentsData) {
    elements.agentCount.textContent = `${agentsData.length} 位坐席`;

    if (agentsData.length === 0) {
        elements.agentCards.innerHTML = '<div class="no-data">暂无坐席</div>';
        return;
    }

    let html = '';
    agentsData.forEach(agent => {
        const statusClass = agent.status.toLowerCase();
        const statusText = {
            'ONLINE': '在线',
            'BUSY': '忙碌',
            'OFFLINE': '离线'
        }[agent.status] || agent.status;
        const loadPercent = agent.maxSessions > 0
            ? Math.round((agent.currentSessions / agent.maxSessions) * 100)
            : 0;

        html += `
            <div class="agent-card ${statusClass}">
                <div class="card-header">
                    <div class="card-title">
                        <div class="card-icon agent-icon">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                                <circle cx="12" cy="7" r="4"/>
                            </svg>
                        </div>
                        <div>
                            <div class="card-name">${agent.agentName || agent.agentId}</div>
                            <div class="card-subname">${agent.agentId}</div>
                        </div>
                    </div>
                    <div class="card-status ${statusClass}">
                        <span class="card-status-dot"></span>
                        ${statusText}
                    </div>
                </div>
                <div class="card-body">
                    <div class="card-info">
                        <span class="card-info-label">当前会话</span>
                        <span class="card-info-value">${agent.currentSessions} / ${agent.maxSessions}</span>
                    </div>
                    <div class="card-info">
                        <span class="card-info-label">负载</span>
                        <span class="card-info-value">
                            <div class="load-bar">
                                <div class="load-fill" style="width: ${loadPercent}%"></div>
                            </div>
                            <span class="load-text">${loadPercent}%</span>
                        </span>
                    </div>
                    <div class="card-info">
                        <span class="card-info-label">后台节点</span>
                        <span class="card-info-value">${agent.backendId || '--'}</span>
                    </div>
                </div>
            </div>
        `;
    });

    elements.agentCards.innerHTML = html;
}

/**
 * 绘制拓扑图 - 显示完整的系统架构
 * 架构说明：
 * - Frontend（客户前置节点）：接收客户 WebSocket 连接，管理会话
 * - Backend（坐席后台节点）：管理坐席 WebSocket 连接，每个 Backend 连接到所有 Frontend
 */
function drawTopology(servicesData, connectionsData, stats) {
    const svg = elements.topologySvg;
    const container = elements.topologyContainer;

    // 获取容器尺寸
    const rect = container.getBoundingClientRect();

    // 分离 Router 和 Backend 服务
    const routers = servicesData.filter(s => s.metadata && s.metadata['service-type'] === 'router');
    const backends = servicesData.filter(s => s.name === 'agent-backend-service' || (s.metadata && s.metadata['service-type'] === 'agent-backend'));

    // 获取连接到当前 Router 的 backend IDs
    const connectedToCurrentRouter = new Set(
        connectionsData
            .filter(c => c.serviceId && c.status === 'ACTIVE')
            .map(c => c.serviceId)
    );

    // 当前 Router ID
    const currentRouterId = getCurrentRouterId();

    // 布局参数
    const routerWidth = 100;
    const routerHeight = 50;
    const backendWidth = 110;
    const backendHeight = 50;
    const padding = 60;
    const layerGap = 220;  // Router 和 Backend 层之间的垂直间距

    // 计算所需宽度和高度
    const nodeSpacing = 120;  // 节点水平间距
    const maxNodes = Math.max(routers.length, backends.length);
    const requiredWidth = maxNodes * nodeSpacing + padding * 2;
    const containerWidth = rect.width > 0 ? rect.width : 1000;
    const width = Math.max(containerWidth, requiredWidth);
    const height = 420;

    console.log('drawTopology - routers:', routers.length, 'backends:', backends.length, 'connectedToCurrent:', connectedToCurrentRouter.size);

    // 清空SVG
    svg.innerHTML = '';
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', '100%');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');

    // 创建Defs用于渐变
    const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
    defs.innerHTML = `
        <linearGradient id="routerGradient" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" style="stop-color:#6366f1;stop-opacity:1" />
            <stop offset="100%" style="stop-color:#8b5cf6;stop-opacity:1" />
        </linearGradient>
        <linearGradient id="backendGradient" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" style="stop-color:#10b981;stop-opacity:1" />
            <stop offset="100%" style="stop-color:#059669;stop-opacity:1" />
        </linearGradient>
        <filter id="glow">
            <feGaussianBlur stdDeviation="2" result="coloredBlur"/>
            <feMerge>
                <feMergeNode in="coloredBlur"/>
                <feMergeNode in="SourceGraphic"/>
            </feMerge>
        </filter>
        <filter id="shadow">
            <feDropShadow dx="0" dy="2" stdDeviation="2" flood-opacity="0.2"/>
        </filter>
    `;
    svg.appendChild(defs);

    // 计算节点位置 - 两层水平居中分布
    const centerX = width / 2;
    const routerY = 80;
    const backendY = routerY + layerGap;

    // 计算路由节点位置（水平均匀分布）
    const routerPositions = [];
    const routerTotalWidth = routers.length * routerWidth + (routers.length - 1) * 20;
    const routerStartX = centerX - routerTotalWidth / 2 + routerWidth / 2;
    routers.forEach((router, i) => {
        routerPositions.push({
            ...router,
            x: routerStartX + i * (routerWidth + 20),
            y: routerY,
            label: router.id.replace('router-', 'R'),
            isCurrent: router.id === currentRouterId
        });
    });

    // 计算后端节点位置（水平均匀分布）
    const backendPositions = [];
    const backendTotalWidth = backends.length * backendWidth + (backends.length - 1) * 20;
    const backendStartX = centerX - backendTotalWidth / 2 + backendWidth / 2;
    backends.forEach((backend, i) => {
        backendPositions.push({
            ...backend,
            x: backendStartX + i * (backendWidth + 20),
            y: backendY,
            label: backend.id.replace('agent-backend-', 'B'),
            isConnected: connectedToCurrentRouter.has(backend.id)
        });
    });

    // 绘制连接线 - 每个 Backend 连接到所有 Router
    // 由于 Backend 连接到所有 Router，所有连接都是实际连接
    backendPositions.forEach(backend => {
        routerPositions.forEach(router => {
            // Backend 已连接到当前 Router = 已连接到所有 Router
            const isConnected = backend.isConnected;

            // 创建连接线
            const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            line.setAttribute('x1', router.x);
            line.setAttribute('y1', router.y + routerHeight / 2);
            line.setAttribute('x2', backend.x);
            line.setAttribute('y2', backend.y - backendHeight / 2);

            if (isConnected) {
                if (router.isCurrent) {
                    // 当前 Router 的连接 - 绿色实线，更粗更亮
                    line.setAttribute('stroke', '#10b981');
                    line.setAttribute('stroke-width', '2.5');
                    line.setAttribute('opacity', '1');
                    line.setAttribute('class', 'connection-line connected');
                } else {
                    // 其他 Router 的连接 - 也是实际连接，绿色实线但稍淡
                    line.setAttribute('stroke', '#34d399');
                    line.setAttribute('stroke-width', '1.5');
                    line.setAttribute('opacity', '0.7');
                }
            } else {
                // Backend 未连接 - 红色虚线
                line.setAttribute('stroke', '#ef4444');
                line.setAttribute('stroke-width', '1.5');
                line.setAttribute('stroke-dasharray', '4,4');
                line.setAttribute('opacity', '0.4');
            }
            svg.appendChild(line);
        });
    });

    // 绘制 Router 节点层标签
    const routerLabel = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    routerLabel.setAttribute('x', '15');
    routerLabel.setAttribute('y', routerY);
    routerLabel.setAttribute('fill', '#6b7280');
    routerLabel.setAttribute('font-size', '11');
    routerLabel.textContent = '客户前端';
    svg.appendChild(routerLabel);

    // 绘制 Router 节点
    routerPositions.forEach(router => {
        const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        g.setAttribute('class', 'topology-node');

        const fillColor = router.isCurrent ? 'url(#routerGradient)' : '#4f46e5';
        const strokeColor = router.isCurrent ? '#a78bfa' : '#6366f1';
        const filterAttr = router.isCurrent ? 'url(#glow)' : '';

        g.innerHTML = `
            <rect x="${router.x - routerWidth/2}" y="${router.y - routerHeight/2}"
                  width="${routerWidth}" height="${routerHeight}" rx="6"
                  fill="${fillColor}" stroke="${strokeColor}" stroke-width="2" ${filterAttr ? `filter="${filterAttr}"` : ''}/>
            <text x="${router.x}" y="${router.y - 4}" fill="#fff" text-anchor="middle"
                  style="font-size:13px;font-weight:600;">${router.label}</text>
            <text x="${router.x}" y="${router.y + 14}" fill="#c4b5fd" text-anchor="middle"
                  style="font-size:10px;">${router.isCurrent ? '当前' : 'Frontend'}</text>
        `;
        svg.appendChild(g);
    });

    // 绘制 Backend 节点层标签
    const backendLabel = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    backendLabel.setAttribute('x', '15');
    backendLabel.setAttribute('y', backendY);
    backendLabel.setAttribute('fill', '#6b7280');
    backendLabel.setAttribute('font-size', '11');
    backendLabel.textContent = '坐席后台';
    svg.appendChild(backendLabel);

    // 绘制 Backend 节点
    backendPositions.forEach(backend => {
        const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        g.setAttribute('class', 'topology-node');

        const statusColor = backend.isConnected ? '#10b981' : '#ef4444';
        const fillColor = backend.isConnected ? 'url(#backendGradient)' : '#374151';
        const statusText = backend.isConnected ? '在线' : '离线';
        const filterAttr = backend.isConnected ? 'url(#glow)' : '';

        g.innerHTML = `
            <rect x="${backend.x - backendWidth/2}" y="${backend.y - backendHeight/2}"
                  width="${backendWidth}" height="${backendHeight}" rx="6"
                  fill="${fillColor}" stroke="${statusColor}" stroke-width="2" ${filterAttr ? `filter="${filterAttr}"` : ''}/>
            <text x="${backend.x}" y="${backend.y - 4}" fill="#fff" text-anchor="middle"
                  style="font-size:13px;font-weight:600;">${backend.label}</text>
            <text x="${backend.x}" y="${backend.y + 14}" fill="${statusColor}" text-anchor="middle"
                  style="font-size:10px;">${statusText}</text>
        `;
        svg.appendChild(g);
    });

    // 绘制统计面板
    const activeSessions = stats?.session?.active || 0;
    const waitingSessions = stats?.session?.waiting || 0;
    const onlineAgents = stats?.agent?.online || 0;
    const totalAgents = stats?.agent?.total || 0;

    const statsPanel = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    statsPanel.innerHTML = `
        <rect x="${width - 165}" y="10" width="155" height="70" rx="6"
              fill="rgba(15, 15, 25, 0.9)" stroke="rgba(99, 102, 241, 0.3)"/>
        <text x="${width - 155}" y="28" fill="#9ca3af" style="font-size:10px;">活跃会话</text>
        <text x="${width - 155}" y="48" fill="#10b981" style="font-size:18px;font-weight:600;">${activeSessions}</text>
        <text x="${width - 115}" y="48" fill="#f59e0b" style="font-size:11px;">等待 ${waitingSessions}</text>
        <text x="${width - 155}" y="68" fill="#9ca3af" style="font-size:10px;">坐席</text>
        <text x="${width - 120}" y="68" fill="#3b82f6" style="font-size:12px;font-weight:500;">${onlineAgents}/${totalAgents}</text>
    `;
    svg.appendChild(statsPanel);

    // 绘制图例
    const legend = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    legend.innerHTML = `
        <rect x="10" y="${height - 28}" width="280" height="22" rx="4" fill="rgba(15, 15, 25, 0.85)"/>
        <circle cx="25" cy="${height - 17}" r="5" fill="#6366f1"/>
        <text x="33" y="${height - 13}" fill="#9ca3af" style="font-size:9px;">Router</text>
        <circle cx="85" cy="${height - 17}" r="5" fill="#10b981"/>
        <text x="93" y="${height - 13}" fill="#9ca3af" style="font-size:9px;">Backend</text>
        <line x1="145" y1="${height - 17}" x2="165" y2="${height - 17}" stroke="#10b981" stroke-width="2"/>
        <text x="170" y="${height - 13}" fill="#9ca3af" style="font-size:9px;">已连接</text>
        <line x1="215" y1="${height - 17}" x2="235" y2="${height - 17}" stroke="#6b7280" stroke-width="1" stroke-dasharray="3,2"/>
        <text x="240" y="${height - 13}" fill="#9ca3af" style="font-size:9px;">Netty连接</text>
    `;
    svg.appendChild(legend);
}

/**
 * 获取当前 Router ID
 */
function getCurrentRouterId() {
    // 尝试从页面配置中获取，默认返回 router-1
    if (typeof window.currentRouterId !== 'undefined') {
        return window.currentRouterId;
    }
    return 'router-1';
}

/**
 * 更新系统状态
 */
function updateSystemStatus(health) {
    if (!health) return;

    const isHealthy = health.netty === 'RUNNING';
    elements.systemStatus.className = `status-indicator ${isHealthy ? '' : 'warning'}`;
    elements.systemStatus.querySelector('.status-text').textContent = isHealthy ? '系统正常' : '部分异常';
}

/**
 * 更新ZooKeeper状态
 */
function updateZkStatus(connected) {
    if (connected) {
        elements.zkStatus.className = 'status-indicator zk-status';
        elements.zkStatus.querySelector('.status-text').textContent = 'ZK: 已连接';
    } else {
        elements.zkStatus.className = 'status-indicator zk-status warning';
        elements.zkStatus.querySelector('.status-text').textContent = 'ZK: 未连接';
    }
}

/**
 * 更新最后更新时间
 */
function updateLastUpdateTime() {
    elements.lastUpdateTime.textContent = formatTime(Date.now());
}

/**
 * 设置折叠功能
 */
function setupCollapsibleSections() {
    document.querySelectorAll('.section-header.collapsible').forEach(header => {
        header.addEventListener('click', () => {
            const targetId = header.dataset.target;
            const content = document.getElementById(targetId);
            const isCollapsed = content.classList.contains('collapsed');

            if (isCollapsed) {
                content.classList.remove('collapsed');
                header.classList.remove('collapsed');
            } else {
                content.classList.add('collapsed');
                header.classList.add('collapsed');
            }
        });
    });
}

/**
 * 设置过滤器事件
 */
function setupFilters() {
    // 搜索按钮
    elements.searchBtn.addEventListener('click', () => {
        currentFilters.sessionId = elements.filterSessionId.value.trim();
        currentFilters.customerId = elements.filterCustomerId.value.trim();
        currentFilters.status = elements.filterStatus.value;

        if (elements.filterStartTime.value) {
            currentFilters.startTime = new Date(elements.filterStartTime.value).getTime();
        } else {
            currentFilters.startTime = null;
        }

        if (elements.filterEndTime.value) {
            currentFilters.endTime = new Date(elements.filterEndTime.value).getTime();
        } else {
            currentFilters.endTime = null;
        }

        currentPage = 0;
        refreshSessions();
    });

    // 重置按钮
    elements.resetBtn.addEventListener('click', () => {
        elements.filterSessionId.value = '';
        elements.filterCustomerId.value = '';
        elements.filterStatus.value = '';
        elements.filterStartTime.value = '';
        elements.filterEndTime.value = '';

        currentFilters = {
            sessionId: '',
            customerId: '',
            agentId: '',
            status: '',
            startTime: null,
            endTime: null
        };

        currentPage = 0;
        refreshSessions();
    });

    // 分页按钮
    elements.prevPage.addEventListener('click', () => {
        if (currentPage > 0) {
            currentPage--;
            refreshSessions();
        }
    });

    elements.nextPage.addEventListener('click', () => {
        const totalPages = Math.ceil(totalElements / pageSize);
        if (currentPage < totalPages - 1) {
            currentPage++;
            refreshSessions();
        }
    });
}

/**
 * 设置弹窗事件
 */
function setupModal() {
    elements.closeModal.addEventListener('click', () => {
        elements.sessionModal.classList.remove('visible');
    });

    elements.sessionModal.addEventListener('click', (e) => {
        if (e.target === elements.sessionModal) {
            elements.sessionModal.classList.remove('visible');
        }
    });

    // 节点指标弹窗
    elements.closeNodeModal.addEventListener('click', () => {
        elements.nodeModal.classList.remove('visible');
    });

    elements.nodeModal.addEventListener('click', (e) => {
        if (e.target === elements.nodeModal) {
            elements.nodeModal.classList.remove('visible');
        }
    });

    // ZooKeeper弹窗
    elements.closeZkModal.addEventListener('click', () => {
        elements.zkModal.classList.remove('visible');
    });

    elements.zkModal.addEventListener('click', (e) => {
        if (e.target === elements.zkModal) {
            elements.zkModal.classList.remove('visible');
        }
    });
}

/**
 * 刷新会话列表
 */
async function refreshSessions() {
    const result = await querySessions();
    sessions = result.content || [];
    totalElements = result.totalElements || 0;

    updateSessionCards(sessions, totalElements);
    updatePagination();
}

/**
 * 刷新所有数据
 */
async function refreshAll() {
    elements.refreshBtn.classList.add('loading');

    try {
        const [stats, health, connectionsData, servicesData, agentsData, zkData, frontendsData, backendsData] = await Promise.all([
            fetchCustomerServiceStats(),
            fetchHealth(),
            fetchConnections(),
            fetchServices(),
            fetchAgents(),
            fetchZookeeperMetadata(),
            fetchFrontendNodes(),
            fetchBackendNodes()
        ]);

        customerServiceStats = stats;
        agents = agentsData;
        connections = connectionsData;
        services = servicesData;
        frontendNodes = frontendsData || [];
        backendNodes = backendsData || [];

        updateStats(stats, connectionsData, servicesData, health, frontendNodes, backendsData);
        updateAgentCards(agentsData);
        updateFrontendCards(frontendNodes);
        updateBackendNodesCards(backendNodes);
        updateSystemStatus(health);
        updateZkStatus(zkData ? zkData.connected : false);
        updateLastUpdateTime();

        // 绘制拓扑图
        drawTopology(servicesData, connectionsData, stats);

        // 刷新会话列表
        await refreshSessions();

    } catch (error) {
        console.error('刷新数据失败:', error);
    } finally {
        elements.refreshBtn.classList.remove('loading');
    }
}

/**
 * 获取前端节点列表
 */
async function fetchFrontendNodes() {
    try {
        const response = await fetch(`${API_BASE}/nodes/frontends`);
        if (!response.ok) throw new Error('获取前端节点失败');
        return await response.json();
    } catch (error) {
        console.error('获取前端节点失败:', error);
        return [];
    }
}

/**
 * 获取后台节点列表
 */
async function fetchBackendNodes() {
    try {
        const response = await fetch(`${API_BASE}/nodes/backends`);
        if (!response.ok) throw new Error('获取后台节点失败');
        return await response.json();
    } catch (error) {
        console.error('获取后台节点失败:', error);
        return [];
    }
}

/**
 * 更新前端节点卡片
 */
function updateFrontendCards(frontends) {
    if (!elements.frontendCount || !elements.frontendCards) return;

    elements.frontendCount.textContent = `${frontends.length} 个节点`;

    if (!frontends || frontends.length === 0) {
        elements.frontendCards.innerHTML = '<div class="no-data">暂无前端节点</div>';
        return;
    }

    let html = '';
    frontends.forEach(node => {
        const isOnline = node.status === 'RUNNING' || node.status === 'ONLINE';
        const statusClass = isOnline ? 'online' : 'offline';
        const statusText = isOnline ? '运行中' : '已停止';
        const uptime = formatDuration(node.uptimeMillis || 0, true);

        html += `
            <div class="node-card frontend-card ${statusClass}" data-node-id="${node.nodeId}">
                <div class="card-header">
                    <div class="card-title">
                        <div class="card-icon frontend-icon">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <rect x="2" y="3" width="20" height="14" rx="2" ry="2"/>
                                <line x1="8" y1="21" x2="16" y2="21"/>
                                <line x1="12" y1="17" x2="12" y2="21"/>
                            </svg>
                        </div>
                        <div>
                            <div class="card-name">${node.nodeId}</div>
                            <div class="card-subname">${node.address || 'localhost'}</div>
                        </div>
                    </div>
                    <div class="card-status ${statusClass}">
                        <span class="card-status-dot"></span>
                        ${statusText}
                    </div>
                </div>
                <div class="card-body">
                    <div class="card-info">
                        <span class="card-info-label">HTTP 端口</span>
                        <span class="card-info-value">${node.httpPort || '-'}</span>
                    </div>
                    <div class="card-info">
                        <span class="card-info-label">Netty 端口</span>
                        <span class="card-info-value">${node.nettyPort || '-'}</span>
                    </div>
                    <div class="card-info">
                        <span class="card-info-label">后台连接</span>
                        <span class="card-info-value">${node.nettyConnections || 0}</span>
                    </div>
                    <div class="card-info">
                        <span class="card-info-label">运行时间</span>
                        <span class="card-info-value">${uptime}</span>
                    </div>
                </div>
            </div>
        `;
    });

    elements.frontendCards.innerHTML = html;
}

/**
 * 更新后台节点卡片
 */
function updateBackendNodesCards(backends) {
    if (!elements.backendCount || !elements.backendCards) return;

    elements.backendCount.textContent = `${backends.length} 个节点`;

    if (!backends || backends.length === 0) {
        elements.backendCards.innerHTML = '<div class="no-data">暂无后台节点</div>';
        return;
    }

    let html = '';
    backends.forEach(node => {
        const statusClass = node.status === 'ACTIVE' ? 'online' : 'offline';
        const statusText = node.status === 'ACTIVE' ? '已连接' : '未连接';

        html += `
            <div class="node-card backend-card ${statusClass}" data-node-id="${node.nodeId}">
                <div class="card-header">
                    <div class="card-title">
                        <div class="card-icon backend-icon">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <rect x="4" y="4" width="16" height="16" rx="2" ry="2"/>
                                <rect x="9" y="9" width="6" height="6"/>
                                <line x1="9" y1="1" x2="9" y2="4"/>
                                <line x1="15" y1="1" x2="15" y2="4"/>
                                <line x1="9" y1="20" x2="9" y2="23"/>
                                <line x1="15" y1="20" x2="15" y2="23"/>
                            </svg>
                        </div>
                        <div>
                            <div class="card-name">${node.nodeId}</div>
                            <div class="card-subname">${node.address || 'N/A'}</div>
                        </div>
                    </div>
                    <div class="card-status ${statusClass}">
                        <span class="card-status-dot"></span>
                        ${statusText}
                    </div>
                </div>
                <div class="card-body">
                    <div class="card-info">
                        <span class="card-info-label">Netty 连接</span>
                        <span class="card-info-value">${node.nettyConnectionCount || 1}</span>
                    </div>
                    <div class="card-info">
                        <span class="card-info-label">在线坐席</span>
                        <span class="card-info-value">${node.agentCount || 0}</span>
                    </div>
                </div>
            </div>
        `;
    });

    elements.backendCards.innerHTML = html;
}

/**
 * 启动自动刷新
 */
function startAutoRefresh() {
    if (autoRefreshTimer) {
        clearInterval(autoRefreshTimer);
    }
    autoRefreshTimer = setInterval(refreshAll, AUTO_REFRESH_INTERVAL);
}

/**
 * 停止自动刷新
 */
function stopAutoRefresh() {
    if (autoRefreshTimer) {
        clearInterval(autoRefreshTimer);
        autoRefreshTimer = null;
    }
}

/**
 * 处理窗口大小变化
 */
function handleResize() {
    // 重绘拓扑图
    if (services.length > 0) {
        drawTopology(services, connections, customerServiceStats);
    }
}

/**
 * 初始化
 */
function init() {
    // 绑定刷新按钮
    elements.refreshBtn.addEventListener('click', refreshAll);

    // 绑定ZooKeeper状态点击
    elements.zkStatus.addEventListener('click', showZookeeperMetadata);

    // 设置折叠功能
    setupCollapsibleSections();

    // 设置过滤器
    setupFilters();

    // 设置弹窗
    setupModal();

    // 初始加载数据
    refreshAll();

    // 启动自动刷新
    startAutoRefresh();

    // 页面可见性变化时刷新
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            refreshAll();
        }
    });

    // 窗口大小变化时重绘拓扑图
    window.addEventListener('resize', handleResize);
}

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', init);

// 页面卸载时清理
window.addEventListener('beforeunload', stopAutoRefresh);
