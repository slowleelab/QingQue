import { defineStore } from "pinia"
import { ref, computed } from "vue"
import type { SessionInfo, AssistPushPayload, AssistPushMessage, ChatMessage } from "@/api/types"

export const useAssistStore = defineStore("assist", () => {
  // 会话列表（Sprint 1 用 mock 数据，后续替换为 API）
  const sessions = ref<SessionInfo[]>([
    { sessionId: "S20260428001", phase: "assist", lastActiveAt: new Date(), customerName: "张三" },
    { sessionId: "S20260428002", phase: "bot", lastActiveAt: new Date(Date.now() - 300000), customerName: "李四" },
    { sessionId: "S20260428003", phase: "handoff", lastActiveAt: new Date(Date.now() - 600000), customerName: "王五" },
  ])

  const activeSessionId = ref<string | null>(null)
  const wsStatus = ref<"connecting" | "connected" | "disconnected" | "error">("disconnected")

  // 按 session 分组存储推送数据
  const pushDataMap = ref<Map<string, AssistPushPayload>>(new Map())

  // 按 session 分组存储对话消息
  const messagesMap = ref<Map<string, ChatMessage[]>>(new Map())

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

  // 初始化 mock 对话数据
  function initMockMessages() {
    messagesMap.value.set("S20260428001", [
      { id: "m1", role: "customer", content: "你好，我想查一下我的信用卡账单", timestamp: new Date(Date.now() - 120000) },
      { id: "m2", role: "bot", content: "您好！请提供您的卡号后四位，我来帮您查询。", timestamp: new Date(Date.now() - 115000) },
      { id: "m3", role: "customer", content: "6225", timestamp: new Date(Date.now() - 60000) },
      { id: "m4", role: "bot", content: "已查到您的账单，本期应还金额为 3,256.80 元，到期还款日为 5 月 15 日。", timestamp: new Date(Date.now() - 55000) },
      { id: "m5", role: "customer", content: "能分期吗？", timestamp: new Date(Date.now() - 10000) },
    ])
    messagesMap.value.set("S20260428002", [
      { id: "m6", role: "customer", content: "积分怎么兑换？", timestamp: new Date(Date.now() - 300000) },
      { id: "m7", role: "bot", content: "您可以在「我的积分」页面选择兑换商品或抵扣年费。", timestamp: new Date(Date.now() - 295000) },
    ])
    messagesMap.value.set("S20260428003", [
      { id: "m8", role: "customer", content: "我要投诉！", timestamp: new Date(Date.now() - 600000) },
      { id: "m9", role: "bot", content: "非常抱歉给您带来不便，正在为您转接人工坐席...", timestamp: new Date(Date.now() - 595000), isTransfer: true },
    ])
  }

  // 仅开发环境初始化 mock 数据，生产构建不包含
  if (import.meta.env.DEV) {
    initMockMessages()
  }

  return {
    sessions, activeSessionId, wsStatus, activePushData, activeMessages, activeSession,
    onPushMessage, addMessage, selectSession, setWsStatus,
  }
})
