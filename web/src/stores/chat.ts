import { defineStore } from "pinia"
import { ref } from "vue"
import { sendMessage } from "@/api/bot"
import type { ChatMessage, ChatRequest } from "@/api/types"

export const useChatStore = defineStore("chat", () => {
  const messages = ref<ChatMessage[]>([])
  const sessionId = ref<string | null>(null)
  const isLoading = ref(false)

  let msgCounter = 0

  async function send(text: string) {
    // 添加用户消息
    const userMsg: ChatMessage = {
      id: `msg-${++msgCounter}`,
      role: "customer" as const,
      content: text,
      timestamp: new Date(),
    }
    messages.value.push(userMsg)
    isLoading.value = true

    try {
      const request: ChatRequest = {
        message: text,
        session_id: sessionId.value ?? undefined,
      }
      const response = await sendMessage(request)
      sessionId.value = response.session_id

      // 添加机器人回复
      const botMsg: ChatMessage = {
        id: `msg-${++msgCounter}`,
        role: "bot",
        content: response.reply,
        timestamp: new Date(),
        intent: response.intent,
        confidence: response.confidence,
        isTransfer: response.is_transfer,
      }
      messages.value.push(botMsg)
    } catch {
      // 错误已在 Axios 拦截器中处理
    } finally {
      isLoading.value = false
    }
  }

  function clearSession() {
    messages.value = []
    sessionId.value = null
  }

  return { messages, sessionId, isLoading, send, clearSession }
})
