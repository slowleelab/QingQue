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
data-testid="chat-input"           v-model="inputText"
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
          @click="handleSend" data-testid="send-button"
        />
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick } from "vue"
import { ElMessage } from "element-plus"
import { ChatDotRound, Promotion } from "@element-plus/icons-vue"
import { useAssistStore } from "@/stores/assist"
import { useWebSocket } from "@/composables/useWebSocket"
import { useDraftText } from "@/composables/useDraftText"
import { useChatSvcPoll } from "@/composables/useChatSvcPoll"
import { sendChatSvcMessage } from "@/api/chat-svc"
import MessageBubble from "@/components/chat/MessageBubble.vue"
import type { SessionPhase } from "@/api/types"

const assistStore = useAssistStore()

// v2.0: per-agent WS, 坐席登录时建连, 不再按会话
const agentId = computed(() => assistStore.currentAgentId)
const { activateSession, notifyAgentMessage } = useWebSocket(agentId)

// 坐席激活会话时通知 Assist
watch(() => assistStore.activeSessionId, (newSid, oldSid) => {
  if (newSid && newSid !== oldSid) {
    activateSession(newSid)
  }
})

const inputText = ref("")
const messageListRef = ref<HTMLElement | null>(null)

// 草稿持久化: 按 sessionId 隔离, 切会话不串
const activeDraft = useDraftText(`draft:agent:${assistStore.activeSessionId ?? "_"}`)
watch(
  () => assistStore.activeSessionId,
  () => { inputText.value = activeDraft.text.value },
)
watch(inputText, (v) => { activeDraft.text.value = v })

const session = computed(() => assistStore.activeSession)

const phaseMap: Record<SessionPhase, { type: "" | "warning" | "success" | "danger"; label: string }> = {
  bot:   { type: "",        label: "机器人服务中" },
  agent: { type: "success", label: "坐席辅助中" },
  ended: { type: "danger",  label: "已结束" },
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

  // 通知 Assist：坐席已回复（合规检测 + 隐式反馈推断）
  notifyAgentMessage(sid, text)

  // 发送坐席消息到 chat-svc (走 axios 包装, 自动 Bearer + 错误拦截)
  try {
    await sendChatSvcMessage(sid, { sender: "agent", content: text })
  } catch { /* chat-svc 不可用时静默; 错误已 ElMessage 提示 */ }

  scrollToBottom()
}

async function scrollToBottom() {
  await nextTick()
  if (messageListRef.value) {
    messageListRef.value.scrollTop = messageListRef.value.scrollHeight
  }
}

// HTTP 长轮询 chat-svc: 抽出到 useChatSvcPoll (B3 拆组件)
useChatSvcPoll({
  sessionId: computed(() => assistStore.activeSessionId),
  onMessage: (m) => {
    const sid = assistStore.activeSessionId
    if (!sid) return
    assistStore.addMessage(sid, m.sender === "agent" ? "agent" : "customer", m.content)
  },
})

// 监听 AssistPanel 通过 store 推过来的草稿片段（如 ScriptCard 采纳）
watch(() => assistStore.pendingInsert, (p) => {
  if (!p) return
  if (assistStore.activeSessionId) {
    // 追加到现有输入末尾；空时直接赋值
    inputText.value = inputText.value ? `${inputText.value}${inputText.value.endsWith("\n") ? "" : "\n"}${p.text}` : p.text
    ElMessage.success("已填充到输入框")
  }
  assistStore.consumePendingInsert()
})

// 选中会话时滚动到底部
watch(() => assistStore.activeSessionId, () => scrollToBottom())
</script>

<style scoped>
.conversation-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--color-bg-surface);
  border-right: 1px solid var(--color-border-lighter);
  min-width: 0;
}

.empty-conversation {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-4);
  color: var(--color-text-secondary);
}

.conversation-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--color-border-lighter);
  background: var(--color-bg-page);
}

.customer-info {
  display: flex;
  align-items: center;
  gap: 10px;
}

.customer-avatar {
  background: var(--color-primary);
  color: var(--color-text-on-primary);
  font-size: var(--fs-base);
}

.customer-detail {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.customer-name {
  font-size: var(--fs-md);
  font-weight: 600;
  color: var(--color-text-primary);
}

.session-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.session-id-text {
  font-size: var(--fs-sm);
  color: var(--color-text-secondary);
  font-family: monospace;
}

.message-list {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-4);
  background: var(--color-msg-list-bg);
}

.no-messages {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--color-text-placeholder);
}

.conversation-input {
  display: flex;
  align-items: flex-end;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid var(--color-border-lighter);
  background: var(--color-bg-surface);
}

.conversation-input :deep(.el-textarea__inner) {
  resize: none;
  border-radius: var(--radius-lg);
}
</style>
