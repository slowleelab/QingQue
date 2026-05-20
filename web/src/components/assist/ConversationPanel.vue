<template>
  <div class="conversation-panel">
    <!-- 无会话选中 -->
    <div v-if="!assistStore.activeSessionId" class="empty-conversation">
      <el-icon :size="64" color="#c0c4cc"><ChatDotRound /></el-icon>
      <p>请从左侧选择一个会话</p>
    </div>

    <template v-else>
      <!-- 客户信息栏 -->
      <div class="conversation-header">
        <div class="customer-info">
          <el-avatar :size="36" class="customer-avatar">{{ session?.customerName?.[0] ?? "?" }}</el-avatar>
          <div class="customer-detail">
            <span class="customer-name">{{ session?.customerName || session?.sessionId }}</span>
            <el-tag :type="phaseTagType" size="small">{{ phaseLabel }}</el-tag>
          </div>
        </div>
        <div class="session-meta">
          <span class="session-id-text">{{ session?.sessionId }}</span>
          <el-tag :type="wsTagType" size="small" effect="dark">{{ wsLabel }}</el-tag>
        </div>
      </div>

      <!-- 消息列表 -->
      <div class="message-list" ref="messageListRef">
        <MessageBubble
          v-for="msg in assistStore.activeMessages"
          :key="msg.id"
          :message="msg"
        />
        <div v-if="assistStore.activeMessages.length === 0" class="no-messages">
          暂无对话消息
        </div>
      </div>

      <!-- 输入区域 -->
      <div class="conversation-input">
        <el-input
          v-model="inputText"
          type="textarea"
          :autosize="{ minRows: 1, maxRows: 3 }"
          placeholder="输入消息回复客户..."
          @keydown.enter.exact.prevent="handleSend"
        />
        <el-button
          type="primary"
          :icon="Promotion"
          circle
          :disabled="!inputText.trim()"
          @click="handleSend"
        />
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick } from "vue"
import { ChatDotRound, Promotion } from "@element-plus/icons-vue"
import { useAssistStore } from "@/stores/assist"
import { useWebSocket } from "@/composables/useWebSocket"
import MessageBubble from "@/components/chat/MessageBubble.vue"
import type { SessionPhase } from "@/api/types"

const assistStore = useAssistStore()
useWebSocket(computed(() => assistStore.activeSessionId))  // 接收 SmartCS 推送

const inputText = ref("")
const messageListRef = ref<HTMLElement | null>(null)

const session = computed(() => assistStore.activeSession)

const phaseMap: Record<SessionPhase, { type: "" | "warning" | "success" | "danger"; label: string }> = {
  bot: { type: "", label: "机器人服务中" },
  handoff: { type: "warning", label: "转接中" },
  assist: { type: "success", label: "坐席辅助中" },
  ended: { type: "danger", label: "已结束" },
}

const phaseTagType = computed(() => phaseMap[session.value?.phase ?? "bot"].type)
const phaseLabel = computed(() => phaseMap[session.value?.phase ?? "bot"].label)

const wsTagType = computed(() => {
  const map: Record<string, "" | "success" | "warning" | "danger"> = {
    connected: "success", connecting: "warning", disconnected: "", error: "danger",
  }
  return map[assistStore.wsStatus] ?? ""
})

const wsLabel = computed(() => {
  const map: Record<string, string> = {
    connected: "已连接", connecting: "连接中", disconnected: "未连接", error: "连接异常",
  }
  return map[assistStore.wsStatus] ?? "未知"
})

async function handleSend() {
  if (!inputText.value.trim() || !assistStore.activeSessionId) return
  const sid = assistStore.activeSessionId
  const text = inputText.value.trim()
  assistStore.addMessage(sid, "agent", text)
  inputText.value = ""

  // 发送坐席消息到 star-connection
  try {
    await fetch(`/api/star/sessions/${sid}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sender: "agent", content: text }),
    })
    // 更新游标：避免轮询拉回自己刚发的消息
    lastPollTimestamp = Date.now()
  } catch { /* star-connection 不可用时静默 */ }

  scrollToBottom()
}

async function scrollToBottom() {
  await nextTick()
  if (messageListRef.value) {
    messageListRef.value.scrollTop = messageListRef.value.scrollHeight
  }
}

// HTTP 长轮询 star-connection 获取新消息（基于时间戳游标，非消费性读取）
// 注意：AI 分析由 star-connection 回调 SmartCS 服务端完成，前端不参与分析链路
let pollActive = false
let lastPollTimestamp = 0  // 游标，只拉取该时间戳之后的消息

async function pollMessages(sessionId: string) {
  pollActive = true
  lastPollTimestamp = 0  // 切换会话时重置游标
  while (pollActive && assistStore.activeSessionId === sessionId) {
    try {
      const url = `/api/star/sessions/${sessionId}/poll?timeout=25000&since=${lastPollTimestamp}`
      const resp = await fetch(url)
      if (!resp.ok) { await new Promise(r => setTimeout(r, 1000)); continue }
      const msgs: Array<{ sender: string; content: string; messageId: string; timestamp: number }> = await resp.json()
      for (const m of msgs) {
        if (m.timestamp > lastPollTimestamp) {
          lastPollTimestamp = m.timestamp
        }
        assistStore.addMessage(sessionId, m.sender === "agent" ? "agent" : "customer", m.content)
      }
    } catch { await new Promise(r => setTimeout(r, 1000)) }
  }
}

watch(() => assistStore.activeSessionId, (newId) => {
  pollActive = false
  lastPollTimestamp = 0
  if (newId) {
    setTimeout(() => pollMessages(newId), 0)
  }
})

// 会话切换时自动滚动到底部
watch(() => assistStore.activeMessages.length, () => scrollToBottom())

// 选中会话时滚动到底部
watch(() => assistStore.activeSessionId, () => scrollToBottom())
</script>

<style scoped>
.conversation-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-right: 1px solid #ebeef5;
  min-width: 0;
}

.empty-conversation {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  color: #909399;
}

.conversation-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #ebeef5;
  background: #fafbfc;
}

.customer-info {
  display: flex;
  align-items: center;
  gap: 10px;
}

.customer-avatar {
  background: #409eff;
  color: #fff;
  font-size: 14px;
}

.customer-detail {
  display: flex;
  align-items: center;
  gap: 8px;
}

.customer-name {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.session-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.session-id-text {
  font-size: 12px;
  color: #909399;
  font-family: monospace;
}

.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  background: #f9f9f9;
}

.no-messages {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #c0c4cc;
}

.conversation-input {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid #ebeef5;
  background: #fff;
}

.conversation-input :deep(.el-textarea__inner) {
  resize: none;
  border-radius: 8px;
}
</style>
