/**
 * 客户测试工具 JavaScript
 * 用于测试在线客服系统的客户端功能
 */

// ============== 配置 ==============
const CONFIG = {
    WS_BASE_URL: `ws://${window.location.host}/ws/customer`,
    API_BASE_URL: '/api/customer',
    RECONNECT_INTERVAL: 5000,
    HEARTBEAT_INTERVAL: 30000,
    NOTIFICATION_DURATION: 5000
};

// ============== 状态管理 ==============
const state = {
    customer: {
        id: null,
        name: null
    },
    session: {
        id: null,
        status: null, // WAITING, ACTIVE, CLOSED
        agentId: null,
        agentName: null
    },
    ws: {
        connection: null,
        connected: false,
        reconnectTimer: null
    },
    messages: [],
    heartbeatTimer: null
};

// ============== DOM 元素 ==============
const DOM = {
    // 登录相关
    loginOverlay: document.getElementById('loginOverlay'),
    loginCustomerId: document.getElementById('loginCustomerId'),
    loginCustomerName: document.getElementById('loginCustomerName'),
    loginBtn: document.getElementById('loginBtn'),

    // 头部相关
    customerNameDisplay: document.getElementById('customerNameDisplay'),
    connectionStatus: document.getElementById('connectionStatus'),

    // 聊天区域
    agentName: document.getElementById('agentName'),
    sessionStatus: document.getElementById('sessionStatus'),
    chatMessages: document.getElementById('chatMessages'),
    emptyChatState: document.getElementById('emptyChatState'),
    messageInput: document.getElementById('messageInput'),
    sendBtn: document.getElementById('sendBtn'),

    // 信息面板
    emptyInfoState: document.getElementById('emptyInfoState'),
    sessionDetails: document.getElementById('sessionDetails'),
    infoSessionId: document.getElementById('infoSessionId'),
    infoSessionStatus: document.getElementById('infoSessionStatus'),
    infoAgentNameDisplay: document.getElementById('infoAgentNameDisplay'),
    infoStartTime: document.getElementById('infoStartTime'),
    infoMessageCount: document.getElementById('infoMessageCount'),
    endSessionBtn: document.getElementById('endSessionBtn'),

    // 其他
    notificationContainer: document.getElementById('notificationContainer')
};

// ============== 工具函数 ==============

/**
 * 格式化时间
 */
function formatTime(timestamp) {
    const date = new Date(timestamp);
    const now = new Date();
    const isToday = date.toDateString() === now.toDateString();

    const hours = date.getHours().toString().padStart(2, '0');
    const minutes = date.getMinutes().toString().padStart(2, '0');

    if (isToday) {
        return `${hours}:${minutes}`;
    } else {
        const month = date.getMonth() + 1;
        const day = date.getDate();
        return `${month}/${day} ${hours}:${minutes}`;
    }
}

/**
 * 生成唯一ID
 */
function generateId() {
    return Date.now().toString(36) + Math.random().toString(36).substr(2);
}

/**
 * 发送浏览器通知
 */
function sendBrowserNotification(title, body) {
    if ('Notification' in window && Notification.permission === 'granted') {
        new Notification(title, { body });
    }
}

/**
 * 请求浏览器通知权限
 */
async function requestNotificationPermission() {
    if ('Notification' in window) {
        await Notification.requestPermission();
    }
}

/**
 * 显示应用内通知
 */
function showNotification(title, message, type = 'info') {
    const icons = {
        success: 'fa-check-circle',
        warning: 'fa-exclamation-triangle',
        error: 'fa-times-circle',
        info: 'fa-info-circle'
    };

    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.innerHTML = `
        <i class="fas ${icons[type]}"></i>
        <div class="notification-content">
            <div class="notification-title">${title}</div>
            <div class="notification-message">${message}</div>
        </div>
    `;

    DOM.notificationContainer.appendChild(notification);

    setTimeout(() => {
        notification.style.opacity = '0';
        notification.style.transform = 'translateX(100%)';
        setTimeout(() => notification.remove(), 300);
    }, CONFIG.NOTIFICATION_DURATION);
}

// ============== API 调用 ==============

/**
 * 创建会话
 */
async function createSession() {
    try {
        const response = await fetch(`${CONFIG.API_BASE_URL}/session`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                customerId: state.customer.id,
                customerName: state.customer.name
            })
        });

        if (!response.ok) {
            throw new Error('创建会话失败');
        }

        const session = await response.json();
        state.session.id = session.sessionId;
        state.session.status = session.status || 'WAITING';

        console.log('会话创建成功:', session);
        return session;
    } catch (error) {
        console.error('创建会话失败:', error);
        throw error;
    }
}

/**
 * 关闭会话
 */
async function closeSession() {
    if (!state.session.id) return;

    try {
        const response = await fetch(`${CONFIG.API_BASE_URL}/session/${state.session.id}`, {
            method: 'DELETE'
        });

        if (!response.ok) {
            throw new Error('关闭会话失败');
        }

        state.session.status = 'CLOSED';
        updateSessionUI();

        showNotification('会话已结束', '感谢您的咨询', 'info');
    } catch (error) {
        console.error('关闭会话失败:', error);
        showNotification('错误', '关闭会话失败', 'error');
    }
}

// ============== WebSocket 连接管理 ==============

/**
 * 连接 WebSocket
 */
function connectWebSocket() {
    if (state.ws.connection) {
        state.ws.connection.close();
    }

    const wsUrl = `${CONFIG.WS_BASE_URL}/${state.session.id}`;

    try {
        state.ws.connection = new WebSocket(wsUrl);

        state.ws.connection.onopen = () => {
            console.log('WebSocket 连接成功');
            state.ws.connected = true;
            clearReconnectTimer();
            startHeartbeat();

            updateConnectionStatus(true);
            showNotification('连接成功', '已连接到客服系统', 'success');
        };

        state.ws.connection.onclose = (event) => {
            console.log('WebSocket 连接关闭:', event);
            state.ws.connected = false;
            stopHeartbeat();

            updateConnectionStatus(false);

            // 如果会话未关闭，尝试重连
            if (state.session.status !== 'CLOSED') {
                scheduleReconnect();
            }
        };

        state.ws.connection.onerror = (error) => {
            console.error('WebSocket 错误:', error);
            state.ws.connected = false;
        };

        state.ws.connection.onmessage = (event) => {
            handleMessage(JSON.parse(event.data));
        };

    } catch (error) {
        console.error('WebSocket 连接失败:', error);
        scheduleReconnect();
    }
}

/**
 * 发送 WebSocket 消息
 */
function sendMessage(message) {
    if (state.ws.connection && state.ws.connected) {
        state.ws.connection.send(JSON.stringify(message));
    }
}

/**
 * 处理收到的消息
 */
function handleMessage(message) {
    console.log('收到消息:', message);

    switch (message.type) {
        case 'SESSION_ASSIGN':
        case 'session_assign':
            handleSessionAssign(message);
            break;
        case 'session_status':
            handleSessionStatus(message);
            break;
        case 'session_created':
            handleSessionCreated(message);
            break;
        case 'CHAT_MESSAGE':
        case 'chat':
            handleChatMessage(message);
            break;
        case 'SESSION_CLOSE':
        case 'session_close':
            handleSessionClose(message);
            break;
        case 'connected':
        case 'PONG':
            // 连接确认或心跳响应
            break;
        default:
            console.log('未知消息类型:', message.type);
    }
}

/**
 * 处理会话创建
 */
function handleSessionCreated(message) {
    console.log('会话创建成功:', message);
    state.session.status = message.status || 'WAITING';
    updateSessionUI();

    // 如果会话已有坐席分配
    if (message.status === 'ACTIVE' && message.agentId) {
        state.session.agentId = message.agentId;
        state.session.agentName = message.agentName || '客服';
        DOM.agentName.textContent = state.session.agentName;
        DOM.messageInput.disabled = false;
        DOM.sendBtn.disabled = false;
        addSystemMessage(`客服 ${state.session.agentName} 已接入`);
    }
}

/**
 * 处理会话状态更新
 */
function handleSessionStatus(message) {
    console.log('会话状态更新:', message);
    state.session.status = message.status || 'WAITING';

    if (message.agentId) {
        state.session.agentId = message.agentId;
        state.session.agentName = message.agentName || '客服';
        DOM.agentName.textContent = state.session.agentName;
        DOM.messageInput.disabled = false;
        DOM.sendBtn.disabled = false;
        addSystemMessage(`客服 ${state.session.agentName} 已接入`);
    }

    updateSessionUI();
}

/**
 * 处理会话分配
 */
function handleSessionAssign(message) {
    state.session.status = 'ACTIVE';
    state.session.agentId = message.agentId;
    state.session.agentName = message.agentName || '客服';

    DOM.agentName.textContent = state.session.agentName;
    updateSessionUI();

    addSystemMessage(`客服 ${state.session.agentName} 已接入`);
    showNotification('客服接入', `客服 ${state.session.agentName} 为您服务`, 'info');

    // 启用输入
    DOM.messageInput.disabled = false;
    DOM.sendBtn.disabled = false;
}

/**
 * 处理聊天消息
 */
function handleChatMessage(message) {
    const msg = {
        id: generateId(),
        senderType: message.senderType || 'AGENT',
        senderId: message.senderId,
        senderName: message.senderName || state.session.agentName || '客服',
        content: message.content,
        timestamp: message.timestamp || Date.now()
    };

    addMessage(msg);

    // 如果不是自己发的消息，发送通知
    if (msg.senderType !== 'CUSTOMER') {
        sendBrowserNotification(`新消息 - ${msg.senderName}`, msg.content);
    }
}

/**
 * 处理会话关闭
 */
function handleSessionClose(message) {
    state.session.status = 'CLOSED';
    updateSessionUI();

    addSystemMessage('会话已结束');
    showNotification('会话结束', '客服已结束本次会话', 'info');

    // 禁用输入
    DOM.messageInput.disabled = true;
    DOM.sendBtn.disabled = true;
    DOM.endSessionBtn.disabled = true;
}

/**
 * 添加消息到列表
 */
function addMessage(message) {
    state.messages.push(message);
    renderMessages();
    scrollToBottom();
    updateMessageCount();
}

/**
 * 添加系统消息
 */
function addSystemMessage(content) {
    addMessage({
        id: generateId(),
        senderType: 'SYSTEM',
        content: content,
        timestamp: Date.now()
    });
}

/**
 * 定时重连
 */
function scheduleReconnect() {
    if (state.ws.reconnectTimer) return;

    state.ws.reconnectTimer = setTimeout(() => {
        state.ws.reconnectTimer = null;
        if (!state.ws.connected && state.session.status !== 'CLOSED') {
            connectWebSocket();
        }
    }, CONFIG.RECONNECT_INTERVAL);
}

/**
 * 清除重连定时器
 */
function clearReconnectTimer() {
    if (state.ws.reconnectTimer) {
        clearTimeout(state.ws.reconnectTimer);
        state.ws.reconnectTimer = null;
    }
}

/**
 * 启动心跳
 */
function startHeartbeat() {
    stopHeartbeat();
    state.heartbeatTimer = setInterval(() => {
        sendMessage({ type: 'PING', timestamp: Date.now() });
    }, CONFIG.HEARTBEAT_INTERVAL);
}

/**
 * 停止心跳
 */
function stopHeartbeat() {
    if (state.heartbeatTimer) {
        clearInterval(state.heartbeatTimer);
        state.heartbeatTimer = null;
    }
}

// ============== UI 渲染 ==============

/**
 * 更新连接状态 UI
 */
function updateConnectionStatus(connected) {
    const statusEl = DOM.connectionStatus;
    if (connected) {
        statusEl.innerHTML = '<i class="fas fa-circle"></i> 已连接';
        statusEl.classList.add('connected');
        statusEl.classList.remove('disconnected');
    } else {
        statusEl.innerHTML = '<i class="fas fa-circle"></i> 未连接';
        statusEl.classList.add('disconnected');
        statusEl.classList.remove('connected');
    }
}

/**
 * 更新会话状态 UI
 */
function updateSessionUI() {
    const statusMap = {
        'WAITING': { text: '等待接入', class: '' },
        'ACTIVE': { text: '会话中', class: 'connected' },
        'CLOSED': { text: '已结束', class: 'closed' }
    };

    const status = statusMap[state.session.status] || { text: state.session.status, class: '' };
    DOM.sessionStatus.textContent = status.text;
    DOM.sessionStatus.className = 'session-status ' + status.class;

    // 更新信息面板
    DOM.emptyInfoState.style.display = 'none';
    DOM.sessionDetails.style.display = 'block';

    DOM.infoSessionId.textContent = state.session.id || '-';
    DOM.infoSessionStatus.textContent = status.text;
    DOM.infoAgentNameDisplay.textContent = state.session.agentName || '等待分配';
    DOM.infoStartTime.textContent = state.session.createTime ? formatTime(state.session.createTime) : '-';
}

/**
 * 渲染消息列表
 */
function renderMessages() {
    DOM.chatMessages.innerHTML = '';

    if (state.messages.length === 0) {
        DOM.chatMessages.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-comment-dots"></i>
                <p>暂无消息</p>
            </div>
        `;
        return;
    }

    state.messages.forEach(msg => {
        const msgEl = createMessageElement(msg);
        DOM.chatMessages.appendChild(msgEl);
    });
}

/**
 * 创建消息元素
 */
function createMessageElement(message) {
    const div = document.createElement('div');

    if (message.senderType === 'SYSTEM') {
        div.className = 'message system';
        div.innerHTML = `<div class="message-content">${escapeHtml(message.content)}</div>`;
    } else if (message.senderType === 'CUSTOMER') {
        div.className = 'message customer';
        const time = formatTime(message.timestamp);
        div.innerHTML = `
            <div class="message-content">
                ${escapeHtml(message.content)}
                <div class="message-time">${time}</div>
            </div>
        `;
    } else {
        div.className = 'message agent';
        const time = formatTime(message.timestamp);
        div.innerHTML = `
            <div class="message-content">
                ${escapeHtml(message.content)}
                <div class="message-time">${time}</div>
            </div>
        `;
    }

    return div;
}

/**
 * HTML 转义
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * 滚动到底部
 */
function scrollToBottom() {
    DOM.chatMessages.scrollTop = DOM.chatMessages.scrollHeight;
}

/**
 * 更新消息计数
 */
function updateMessageCount() {
    DOM.infoMessageCount.textContent = state.messages.length;
}

// ============== 事件处理 ==============

/**
 * 发送聊天消息
 */
function sendChatMessage() {
    const content = DOM.messageInput.value.trim();
    if (!content || !state.session.id || state.session.status === 'CLOSED') return;

    const message = {
        type: 'CHAT_MESSAGE',
        sessionId: state.session.id,
        senderType: 'CUSTOMER',
        senderId: state.customer.id,
        senderName: state.customer.name,
        content: content,
        timestamp: Date.now()
    };

    sendMessage(message);

    // 添加到本地消息列表
    addMessage({
        id: generateId(),
        ...message
    });

    DOM.messageInput.value = '';
}

/**
 * 登录并创建会话
 */
async function handleLogin() {
    const customerId = DOM.loginCustomerId.value.trim();
    const customerName = DOM.loginCustomerName.value.trim();

    if (!customerId || !customerName) {
        showNotification('登录失败', '请输入客户ID和名称', 'error');
        return;
    }

    state.customer.id = customerId;
    state.customer.name = customerName;

    DOM.customerNameDisplay.textContent = customerName;

    try {
        // 创建会话
        const session = await createSession();

        // 隐藏登录界面
        DOM.loginOverlay.classList.add('hidden');

        // 检查会话状态 - 如果已有坐席分配，直接启用聊天
        if (session.status === 'ACTIVE' && session.agentId) {
            state.session.agentId = session.agentId;
            state.session.agentName = session.agentName || '客服';
            DOM.agentName.textContent = state.session.agentName;
            DOM.messageInput.disabled = false;
            DOM.sendBtn.disabled = false;
            addSystemMessage(`客服 ${state.session.agentName} 已接入`);
        } else {
            // 等待坐席分配
            addSystemMessage('正在为您接入客服，请稍候...');
        }

        // 连接 WebSocket
        connectWebSocket();

        // 请求通知权限
        requestNotificationPermission();

        showNotification('登录成功', '正在接入客服系统', 'success');
    } catch (error) {
        showNotification('错误', '创建会话失败，请重试', 'error');
    }
}

/**
 * 结束会话
 */
async function handleEndSession() {
    if (confirm('确定要结束会话吗?')) {
        await closeSession();
        stopHeartbeat();
        if (state.ws.connection) {
            state.ws.connection.close();
        }
    }
}

// ============== 初始化 ==============

/**
 * 绑定事件
 */
function bindEvents() {
    // 登录
    DOM.loginBtn.addEventListener('click', handleLogin);
    DOM.loginCustomerId.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') handleLogin();
    });
    DOM.loginCustomerName.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') handleLogin();
    });

    // 发送消息
    DOM.sendBtn.addEventListener('click', sendChatMessage);
    DOM.messageInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendChatMessage();
        }
    });

    // 结束会话
    DOM.endSessionBtn.addEventListener('click', handleEndSession);

    // 自动调整输入框高度
    DOM.messageInput.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    });

    // 页面可见性变化时更新未读计数
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
            // 页面重新可见时可以做一些处理
        }
    });

    // 窗口关闭前清理
    window.addEventListener('beforeunload', () => {
        stopHeartbeat();
        if (state.ws.connection) {
            state.ws.connection.close();
        }
    });
}

/**
 * 初始化
 */
function init() {
    bindEvents();
    updateConnectionStatus(false);
}

// 启动应用
document.addEventListener('DOMContentLoaded', init);
