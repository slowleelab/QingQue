/**
 * 坐席工作台 JavaScript
 * 处理WebSocket连接、会话管理、消息收发和未读消息红点
 */

// ============== 配置 ==============
// 使用外部配置文件 (config.js)，如果未加载则使用默认配置
const CONFIG = {
    WS_BASE_URL: (typeof AGENT_CONFIG !== 'undefined' && AGENT_CONFIG.WS_BASE_URL)
        ? AGENT_CONFIG.WS_BASE_URL
        : `ws://${window.location.host}/ws/agent`,
    API_BASE_URL: (typeof AGENT_CONFIG !== 'undefined' && AGENT_CONFIG.API_BASE_URL)
        ? AGENT_CONFIG.API_BASE_URL
        : '/api/agent',
    RECONNECT_INTERVAL: (typeof AGENT_CONFIG !== 'undefined' && AGENT_CONFIG.RECONNECT_INTERVAL)
        ? AGENT_CONFIG.RECONNECT_INTERVAL
        : 5000,
    HEARTBEAT_INTERVAL: (typeof AGENT_CONFIG !== 'undefined' && AGENT_CONFIG.HEARTBEAT_INTERVAL)
        ? AGENT_CONFIG.HEARTBEAT_INTERVAL
        : 30000,
    NOTIFICATION_DURATION: (typeof AGENT_CONFIG !== 'undefined' && AGENT_CONFIG.NOTIFICATION_DURATION)
        ? AGENT_CONFIG.NOTIFICATION_DURATION
        : 5000
};

// ============== 状态管理 ==============
const state = {
    agent: {
        id: null,
        name: null,
        status: 'OFFLINE' // OFFLINE, ONLINE, BUSY
    },
    ws: {
        connection: null,
        connected: false,
        reconnectTimer: null
    },
    sessions: new Map(), // sessionId -> session object
    currentSessionId: null,
    messages: new Map(), // sessionId -> message array
    unreadCounts: new Map(), // sessionId -> unread count
    heartbeatTimer: null
};

// ============== DOM 元素 ==============
const DOM = {
    // 登录相关
    loginOverlay: document.getElementById('loginOverlay'),
    loginAgentId: document.getElementById('loginAgentId'),
    loginAgentName: document.getElementById('loginAgentName'),
    loginBtn: document.getElementById('loginBtn'),

    // 头部相关
    agentName: document.getElementById('agentName'),
    agentId: document.getElementById('agentId'),
    agentStatusBadge: document.getElementById('agentStatusBadge'),
    toggleStatusBtn: document.getElementById('toggleStatusBtn'),
    logoutBtn: document.getElementById('logoutBtn'),

    // 会话列表
    sessionList: document.getElementById('sessionList'),
    emptySessionState: document.getElementById('emptySessionState'),
    activeSessionCount: document.getElementById('activeSessionCount'),
    waitingSessionCount: document.getElementById('waitingSessionCount'),

    // 聊天区域
    chatHeader: document.getElementById('chatHeader'),
    currentCustomerName: document.getElementById('currentCustomerName'),
    currentSessionStatus: document.getElementById('currentSessionStatus'),
    chatMessages: document.getElementById('chatMessages'),
    emptyChatState: document.getElementById('emptyChatState'),
    messageInput: document.getElementById('messageInput'),
    sendBtn: document.getElementById('sendBtn'),
    transferBtn: document.getElementById('transferBtn'),
    closeSessionBtn: document.getElementById('closeSessionBtn'),

    // 客户信息
    customerInfoContent: document.getElementById('customerInfoContent'),
    emptyCustomerState: document.getElementById('emptyCustomerState'),
    customerDetails: document.getElementById('customerDetails'),
    infoCustomerId: document.getElementById('infoCustomerId'),
    infoCustomerName: document.getElementById('infoCustomerName'),
    infoSessionId: document.getElementById('infoSessionId'),
    infoSessionStatus: document.getElementById('infoSessionStatus'),
    infoStartTime: document.getElementById('infoStartTime'),
    infoMessageCount: document.getElementById('infoMessageCount'),

    // 其他
    notificationContainer: document.getElementById('notificationContainer'),
    notificationSound: document.getElementById('notificationSound')
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
 * 播放通知声音
 */
function playNotificationSound() {
    try {
        DOM.notificationSound.currentTime = 0;
        DOM.notificationSound.play().catch(() => {
            // 忽略自动播放限制错误
        });
    } catch (e) {
        console.warn('播放通知声音失败:', e);
    }
}

/**
 * 发送浏览器通知
 */
function sendBrowserNotification(title, body, icon) {
    if ('Notification' in window && Notification.permission === 'granted') {
        new Notification(title, { body, icon });
    }
}

/**
 * 请求浏览器通知权限
 */
async function requestNotificationPermission() {
    if ('Notification' in window) {
        const permission = await Notification.requestPermission();
        return permission === 'granted';
    }
    return false;
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

// ============== 未读消息红点管理 ==============

/**
 * 增加未读消息计数
 */
function incrementUnreadCount(sessionId) {
    // 如果当前正在查看该会话，不增加未读
    if (state.currentSessionId === sessionId) {
        return;
    }

    const currentCount = state.unreadCounts.get(sessionId) || 0;
    state.unreadCounts.set(sessionId, currentCount + 1);
    updateUnreadBadge(sessionId);
}

/**
 * 清除未读消息计数
 */
function clearUnreadCount(sessionId) {
    state.unreadCounts.set(sessionId, 0);
    updateUnreadBadge(sessionId);
}

/**
 * 更新未读消息红点显示
 */
function updateUnreadBadge(sessionId) {
    const sessionItem = document.querySelector(`[data-session-id="${sessionId}"]`);
    if (!sessionItem) return;

    let badge = sessionItem.querySelector('.unread-badge');
    const count = state.unreadCounts.get(sessionId) || 0;

    if (count > 0) {
        if (!badge) {
            badge = document.createElement('div');
            badge.className = 'unread-badge';
            sessionItem.appendChild(badge);
        }
        badge.textContent = count > 99 ? '99+' : count;
        badge.classList.remove('hidden');
        // 添加脉冲动画
        badge.classList.remove('pulse');
        void badge.offsetWidth; // 触发重排
        badge.classList.add('pulse');
    } else if (badge) {
        badge.classList.add('hidden');
    }

    // 更新页面标题显示总未读数
    updatePageTitle();
}

/**
 * 更新页面标题显示总未读数
 */
function updatePageTitle() {
    let totalUnread = 0;
    state.unreadCounts.forEach(count => {
        totalUnread += count;
    });

    const baseTitle = '坐席工作台 - 在线客服系统';
    if (totalUnread > 0) {
        document.title = `(${totalUnread > 99 ? '99+' : totalUnread}) ${baseTitle}`;
    } else {
        document.title = baseTitle;
    }
}

/**
 * 获取总未读消息数
 */
function getTotalUnreadCount() {
    let total = 0;
    state.unreadCounts.forEach(count => {
        total += count;
    });
    return total;
}

// ============== WebSocket 连接管理 ==============

/**
 * 连接 WebSocket
 */
function connectWebSocket() {
    if (state.ws.connection) {
        state.ws.connection.close();
    }

    const wsUrl = `${CONFIG.WS_BASE_URL}/${state.agent.id}`;

    try {
        state.ws.connection = new WebSocket(wsUrl);

        state.ws.connection.onopen = () => {
            console.log('WebSocket 连接成功');
            state.ws.connected = true;
            clearReconnectTimer();
            startHeartbeat();

            // 发送坐席注册消息
            sendAgentRegister();

            showNotification('连接成功', '已连接到服务器', 'success');
        };

        state.ws.connection.onclose = (event) => {
            console.log('WebSocket 连接关闭:', event);
            state.ws.connected = false;
            stopHeartbeat();
            scheduleReconnect();
            showNotification('连接断开', '正在尝试重新连接...', 'warning');
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
 * 发送坐席注册消息
 */
function sendAgentRegister() {
    sendMessage({
        type: 'AGENT_REGISTER',
        agentId: state.agent.id,
        agentName: state.agent.name,
        timestamp: Date.now()
    });
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
            handleSessionAssign(message);
            break;
        case 'CHAT_MESSAGE':
            handleChatMessage(message);
            break;
        case 'SESSION_CLOSE':
            handleSessionClose(message);
            break;
        case 'AGENT_STATUS':
            handleAgentStatus(message);
            break;
        case 'PONG':
            // 心跳响应
            break;
        default:
            console.log('未知消息类型:', message.type);
    }
}

/**
 * 处理会话分配
 */
function handleSessionAssign(message) {
    const session = message.session || {
        sessionId: message.sessionId,
        customerId: message.customerId,
        customerName: message.customerName || '客户',
        status: 'WAITING',
        createTime: Date.now()
    };

    state.sessions.set(session.sessionId, session);
    state.messages.set(session.sessionId, []);
    state.unreadCounts.set(session.sessionId, 0);

    renderSessionList();
    addSystemMessage(session.sessionId, `新会话: 客户 ${session.customerName} 进入等待`);

    // 播放提示音并发送通知
    playNotificationSound();
    sendBrowserNotification('新会话', `客户 ${session.customerName} 已接入`);

    showNotification('新会话', `客户 ${session.customerName} 已分配给您`, 'info');
}

/**
 * 处理聊天消息
 */
function handleChatMessage(message) {
    const sessionId = message.sessionId;
    if (!state.sessions.has(sessionId)) {
        console.warn('未知会话:', sessionId);
        return;
    }

    const session = state.sessions.get(sessionId);

    // 更新会话预览
    session.lastMessage = message.content;
    session.lastMessageTime = message.timestamp;

    // 添加消息到列表
    addMessageToList(sessionId, {
        id: generateId(),
        sessionId: sessionId,
        senderType: message.senderType || 'CUSTOMER',
        senderId: message.senderId,
        senderName: message.senderName || session.customerName,
        content: message.content,
        timestamp: message.timestamp || Date.now()
    });

    // 如果不是当前会话，增加未读计数
    if (state.currentSessionId !== sessionId) {
        incrementUnreadCount(sessionId);
        playNotificationSound();
        sendBrowserNotification(`新消息 - ${session.customerName}`, message.content);
    }

    renderSessionList();

    // 如果是当前会话，渲染消息
    if (state.currentSessionId === sessionId) {
        renderMessages(sessionId);
        scrollToBottom();
    }
}

/**
 * 处理会话关闭
 */
function handleSessionClose(message) {
    const sessionId = message.sessionId;
    const session = state.sessions.get(sessionId);

    if (session) {
        session.status = 'CLOSED';
        addSystemMessage(sessionId, '会话已结束');
        renderSessionList();

        if (state.currentSessionId === sessionId) {
            updateChatHeader(session);
        }

        showNotification('会话结束', `与客户 ${session.customerName} 的会话已结束`, 'info');
    }
}

/**
 * 处理坐席状态更新
 */
function handleAgentStatus(message) {
    // 服务器确认状态更新
    state.agent.status = message.status;
    updateAgentStatusUI();
}

/**
 * 添加消息到列表
 */
function addMessageToList(sessionId, message) {
    if (!state.messages.has(sessionId)) {
        state.messages.set(sessionId, []);
    }
    state.messages.get(sessionId).push(message);
}

/**
 * 添加系统消息
 */
function addSystemMessage(sessionId, content) {
    addMessageToList(sessionId, {
        id: generateId(),
        sessionId: sessionId,
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
        if (!state.ws.connected) {
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
 * 渲染会话列表
 */
function renderSessionList() {
    const sessions = Array.from(state.sessions.values());

    // 更新统计
    const activeCount = sessions.filter(s => s.status === 'ACTIVE').length;
    const waitingCount = sessions.filter(s => s.status === 'WAITING').length;
    DOM.activeSessionCount.textContent = activeCount;
    DOM.waitingSessionCount.textContent = waitingCount;

    // 清空列表
    DOM.sessionList.innerHTML = '';

    if (sessions.length === 0) {
        DOM.sessionList.appendChild(DOM.emptySessionState.cloneNode(true));
        return;
    }

    // 按时间排序（最新的在前）
    sessions.sort((a, b) => {
        const timeA = a.lastMessageTime || a.createTime || 0;
        const timeB = b.lastMessageTime || b.createTime || 0;
        return timeB - timeA;
    });

    // 渲染会话项
    sessions.forEach(session => {
        const item = createSessionItem(session);
        DOM.sessionList.appendChild(item);
    });
}

/**
 * 创建会话列表项
 */
function createSessionItem(session) {
    const item = document.createElement('div');
    item.className = 'session-item';
    item.dataset.sessionId = session.sessionId;

    if (state.currentSessionId === session.sessionId) {
        item.classList.add('active');
    }
    if (session.status === 'WAITING') {
        item.classList.add('waiting');
    }

    const initial = (session.customerName || '客').charAt(0).toUpperCase();
    const lastMessage = session.lastMessage || '暂无消息';
    const time = formatTime(session.lastMessageTime || session.createTime);

    item.innerHTML = `
        <div class="session-avatar">${initial}</div>
        <div class="session-content">
            <div class="session-header">
                <span class="session-name">${session.customerName || '客户'}</span>
                <span class="session-time">${time}</span>
            </div>
            <div class="session-preview">${lastMessage}</div>
        </div>
    `;

    // 添加未读红点
    const unreadCount = state.unreadCounts.get(session.sessionId) || 0;
    if (unreadCount > 0) {
        const badge = document.createElement('div');
        badge.className = 'unread-badge';
        badge.textContent = unreadCount > 99 ? '99+' : unreadCount;
        item.appendChild(badge);
    }

    item.addEventListener('click', () => selectSession(session.sessionId));

    return item;
}

/**
 * 选择会话
 */
function selectSession(sessionId) {
    state.currentSessionId = sessionId;
    clearUnreadCount(sessionId);

    const session = state.sessions.get(sessionId);
    if (!session) return;

    // 更新UI
    renderSessionList();
    updateChatHeader(session);
    renderMessages(sessionId);
    updateCustomerInfo(session);

    // 启用输入
    DOM.messageInput.disabled = session.status === 'CLOSED';
    DOM.sendBtn.disabled = session.status === 'CLOSED';
    DOM.transferBtn.disabled = session.status === 'CLOSED';
    DOM.closeSessionBtn.disabled = session.status === 'CLOSED';

    // 如果是等待状态，自动接受会话
    if (session.status === 'WAITING') {
        acceptSession(sessionId);
    }
}

/**
 * 接受会话
 */
function acceptSession(sessionId) {
    const session = state.sessions.get(sessionId);
    if (!session) return;

    session.status = 'ACTIVE';

    sendMessage({
        type: 'SESSION_ACCEPT',
        sessionId: sessionId,
        agentId: state.agent.id,
        timestamp: Date.now()
    });

    renderSessionList();
    updateChatHeader(session);
    addSystemMessage(sessionId, '会话已开始');
}

/**
 * 更新聊天头部
 */
function updateChatHeader(session) {
    DOM.currentCustomerName.textContent = session.customerName || '客户';

    const statusMap = {
        'WAITING': { text: '等待中', class: 'waiting' },
        'ACTIVE': { text: '进行中', class: '' },
        'CLOSED': { text: '已结束', class: 'closed' }
    };

    const status = statusMap[session.status] || { text: session.status, class: '' };
    DOM.currentSessionStatus.textContent = status.text;
    DOM.currentSessionStatus.className = 'session-status ' + status.class;
}

/**
 * 渲染消息列表
 */
function renderMessages(sessionId) {
    const messages = state.messages.get(sessionId) || [];

    DOM.chatMessages.innerHTML = '';

    if (messages.length === 0) {
        DOM.chatMessages.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-comment-dots"></i>
                <p>暂无消息</p>
            </div>
        `;
        return;
    }

    messages.forEach(msg => {
        const msgEl = createMessageElement(msg);
        DOM.chatMessages.appendChild(msgEl);
    });
}

/**
 * 创建消息元素
 */
function createMessageElement(message) {
    const div = document.createElement('div');
    div.className = `message ${message.senderType.toLowerCase()}`;

    if (message.senderType === 'SYSTEM') {
        div.innerHTML = `<div class="message-content">${message.content}</div>`;
    } else {
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
 * 更新客户信息面板
 */
function updateCustomerInfo(session) {
    DOM.emptyCustomerState.style.display = 'none';
    DOM.customerDetails.style.display = 'block';

    DOM.infoCustomerId.textContent = session.customerId || '-';
    DOM.infoCustomerName.textContent = session.customerName || '-';
    DOM.infoSessionId.textContent = session.sessionId;

    const statusMap = {
        'WAITING': '等待中',
        'ACTIVE': '进行中',
        'CLOSED': '已结束'
    };
    DOM.infoSessionStatus.textContent = statusMap[session.status] || session.status;
    DOM.infoStartTime.textContent = session.createTime ? formatTime(session.createTime) : '-';

    const messages = state.messages.get(session.sessionId) || [];
    DOM.infoMessageCount.textContent = messages.length;
}

/**
 * 更新坐席状态 UI
 */
function updateAgentStatusUI() {
    const statusMap = {
        'ONLINE': { text: '在线', class: 'online', btnText: '切换忙碌', btnClass: 'online' },
        'BUSY': { text: '忙碌', class: 'busy', btnText: '切换离线', btnClass: 'busy' },
        'OFFLINE': { text: '离线', class: 'offline', btnText: '上线', btnClass: '' }
    };

    const status = statusMap[state.agent.status] || statusMap['OFFLINE'];

    DOM.agentStatusBadge.innerHTML = `<i class="fas fa-circle"></i> ${status.text}`;
    DOM.agentStatusBadge.className = `agent-status-badge ${status.class}`;

    DOM.toggleStatusBtn.innerHTML = `<i class="fas fa-toggle-on"></i> ${status.btnText}`;
    DOM.toggleStatusBtn.className = `btn btn-status ${status.btnClass}`;
}

// ============== 事件处理 ==============

/**
 * 发送聊天消息
 */
function sendChatMessage() {
    const content = DOM.messageInput.value.trim();
    if (!content || !state.currentSessionId || !state.ws.connected) return;

    const session = state.sessions.get(state.currentSessionId);
    if (!session || session.status === 'CLOSED') return;

    const message = {
        type: 'CHAT_MESSAGE',
        sessionId: state.currentSessionId,
        senderType: 'AGENT',
        senderId: state.agent.id,
        senderName: state.agent.name,
        content: content,
        timestamp: Date.now()
    };

    sendMessage(message);

    // 添加到本地消息列表
    addMessageToList(state.currentSessionId, {
        id: generateId(),
        ...message
    });

    // 更新会话预览
    session.lastMessage = content;
    session.lastMessageTime = message.timestamp;

    // 渲染
    renderMessages(state.currentSessionId);
    renderSessionList();
    scrollToBottom();

    DOM.messageInput.value = '';
}

/**
 * 切换坐席状态
 */
function toggleAgentStatus() {
    const statusOrder = ['ONLINE', 'BUSY', 'OFFLINE'];
    const currentIndex = statusOrder.indexOf(state.agent.status);
    const nextIndex = (currentIndex + 1) % statusOrder.length;
    const newStatus = statusOrder[nextIndex];

    sendMessage({
        type: 'AGENT_STATUS',
        agentId: state.agent.id,
        status: newStatus,
        timestamp: Date.now()
    });

    state.agent.status = newStatus;
    updateAgentStatusUI();
}

/**
 * 关闭会话
 */
function closeCurrentSession() {
    if (!state.currentSessionId) return;

    const session = state.sessions.get(state.currentSessionId);
    if (!session || session.status === 'CLOSED') return;

    sendMessage({
        type: 'SESSION_CLOSE',
        sessionId: state.currentSessionId,
        agentId: state.agent.id,
        timestamp: Date.now()
    });

    session.status = 'CLOSED';
    addSystemMessage(state.currentSessionId, '会话已结束');
    renderSessionList();
    updateChatHeader(session);

    DOM.messageInput.disabled = true;
    DOM.sendBtn.disabled = true;
    DOM.transferBtn.disabled = true;
    DOM.closeSessionBtn.disabled = true;

    showNotification('会话结束', `与客户 ${session.customerName} 的会话已结束`, 'info');
}

/**
 * 登录
 */
function handleLogin() {
    const agentId = DOM.loginAgentId.value.trim();
    const agentName = DOM.loginAgentName.value.trim();

    if (!agentId || !agentName) {
        showNotification('登录失败', '请输入坐席ID和名称', 'error');
        return;
    }

    state.agent.id = agentId;
    state.agent.name = agentName;
    state.agent.status = 'ONLINE';

    // 更新 UI
    DOM.agentName.textContent = agentName;
    DOM.agentId.textContent = agentId;
    updateAgentStatusUI();

    // 隐藏登录界面
    DOM.loginOverlay.classList.add('hidden');

    // 连接 WebSocket
    connectWebSocket();

    // 请求通知权限
    requestNotificationPermission();

    showNotification('登录成功', `欢迎, ${agentName}`, 'success');
}

/**
 * 登出
 */
function handleLogout() {
    if (confirm('确定要退出吗?')) {
        stopHeartbeat();
        if (state.ws.connection) {
            state.ws.connection.close();
        }

        state.agent.status = 'OFFLINE';
        state.sessions.clear();
        state.messages.clear();
        state.unreadCounts.clear();
        state.currentSessionId = null;

        DOM.loginOverlay.classList.remove('hidden');
        updateAgentStatusUI();

        showNotification('已退出', '您已成功退出', 'info');
    }
}

// ============== 初始化 ==============

/**
 * 绑定事件
 */
function bindEvents() {
    // 登录
    DOM.loginBtn.addEventListener('click', handleLogin);
    DOM.loginAgentId.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') handleLogin();
    });
    DOM.loginAgentName.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') handleLogin();
    });

    // 状态切换
    DOM.toggleStatusBtn.addEventListener('click', toggleAgentStatus);

    // 登出
    DOM.logoutBtn.addEventListener('click', handleLogout);

    // 发送消息
    DOM.sendBtn.addEventListener('click', sendChatMessage);
    DOM.messageInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendChatMessage();
        }
    });

    // 关闭会话
    DOM.closeSessionBtn.addEventListener('click', closeCurrentSession);

    // 自动调整输入框高度
    DOM.messageInput.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    });

    // 页面可见性变化时更新未读计数
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden && state.currentSessionId) {
            clearUnreadCount(state.currentSessionId);
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
    updateAgentStatusUI();
    renderSessionList();

    // 检查通知权限
    if ('Notification' in window && Notification.permission === 'default') {
        // 可以显示提示让用户授权通知
    }
}

// 启动应用
document.addEventListener('DOMContentLoaded', init);
