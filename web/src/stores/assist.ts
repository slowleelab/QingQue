import { defineStore } from "pinia"
import { ref, computed } from "vue"
import type { SessionInfo, AssistPushPayload, AssistPushMessage, ChatMessage } from "@/api/types"

// chat-svc 会话 API
const CHAT_SVC_SESSIONS_URL = "/api/chat-svc/monitor/customer-service/sessions"

export const useAssistStore = defineStore("assist", () => {
  const sessions = ref<SessionInfo[]>([])
  const fetching = ref(false)

  const currentAgentId = ref<string | null>(null)  // v2.0: per-agent WS
  const activeSessionId = ref<string | null>(null)
  const wsStatus = ref<"connecting" | "connected" | "disconnected" | "error">("disconnected")

  // 按 session 分组存储推送数据
  const pushDataMap = ref<Map<string, AssistPushPayload>>(new Map())

  // 按 session 分组存储对话消息
  const messagesMap = ref<Map<string, ChatMessage[]>>(new Map())

  // 跨组件注入的草稿片段：AssistPanel 的"采纳"按钮通过 store 推送给 ConversationPanel 输入框
  // nonce 字段用于在同一文本连续点击时强制触发 watch (避免文本相同时 watcher 不触发)
  const pendingInsert = ref<{ text: string; nonce: number } | null>(null)

  const activePushData = computed(() => {
    if (!activeSessionId.value) return null
    return pushDataMap.value.get(activeSessionId.value) ?? null
  })

  const activeMessages = computed(() => {
    if (!activeSessionId.value) return []
    return messagesMap.value.get(activeSessionId.value) ?? []
  })

  const activeSession = computed(() => {
    if (!activeSessionId.value) return null
    return sessions.value.find((s) => s.sessionId === activeSessionId.value) ?? null
  })

  let msgCounter = 0

  function onPushMessage(msg: AssistPushMessage) {
    pushDataMap.value.set(msg.session_id, msg.payload)
    const session = sessions.value.find((s) => s.sessionId === msg.session_id)
    if (session) {
      session.lastActiveAt = new Date(msg.timestamp)
    }
  }

  function addMessage(sessionId: string, role: ChatMessage["role"], content: string, extra?: Partial<ChatMessage>) {
    const messages = messagesMap.value.get(sessionId) ?? []
    messages.push({
      id: `msg-${++msgCounter}`,
      role,
      content,
      timestamp: new Date(),
      ...extra,
    })
    messagesMap.value.set(sessionId, messages)
  }

  function selectSession(id: string) {
    activeSessionId.value = id
  }

  function setWsStatus(status: "connecting" | "connected" | "disconnected" | "error") {
    wsStatus.value = status
  }

  // 草稿注入：从 AssistPanel 的"采纳/修改"按钮推到 ConversationPanel 输入框
  function insertDraftText(text: string) {
    pendingInsert.value = { text, nonce: Date.now() }
  }

  // ConversationPanel 消费后清空，避免下次进入会话时重复触发
  function consumePendingInsert() {
    pendingInsert.value = null
  }

  // 从 chat-svc 拉取实时会话列表
  async function fetchSessions() {
    fetching.value = true
    try {
      const resp = await fetch(CHAT_SVC_SESSIONS_URL)
      if (!resp.ok) return
      const data: Array<{
        sessionId: string
        status: string
        agentId: string | null
        createTime: number
        customerId: string | null
      }> = await resp.json()

      // 转换 chat-svc 会话为前端 SessionInfo
      const chatSessions: SessionInfo[] = data
        .filter((s) => s.status !== "CLOSED")
        .map((s) => ({
          sessionId: s.sessionId,
          phase: s.status === "ACTIVE" ? "agent" as const : "bot" as const,
          lastActiveAt: new Date(s.createTime),
          customerName: s.customerId || "访客",
          agentId: s.agentId || undefined,
        }))

      // 合并已有会话和 chat-svc 会话
      const existingIds = new Set(sessions.value.map((s) => s.sessionId))
      for (const s of chatSessions) {
        if (!existingIds.has(s.sessionId)) {
          sessions.value.push(s)
        }
      }
    } catch {
      // chat-svc 不可用时静默失败
    } finally {
      fetching.value = false
    }
  }

  // 启动时拉取，之后每 5 秒轮询
  fetchSessions()
  const pollTimer = setInterval(fetchSessions, 5000)

  // 页面卸载时清理定时器
  if (typeof window !== "undefined") {
    window.addEventListener("beforeunload", () => clearInterval(pollTimer))
  }

  return {
    currentAgentId,
    sessions, activeSessionId, wsStatus, activePushData, activeMessages, activeSession,
    pendingInsert,
    fetching, fetchSessions,
    onPushMessage, addMessage, selectSession, setWsStatus, insertDraftText, consumePendingInsert,
  }
})
