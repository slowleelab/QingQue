import { defineStore } from "pinia"
import { ref } from "vue"
import { sendMessage, pollReply } from "@/api/bot"
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
      // 发送消息
      const sendResp = await sendMessage(request)
      sessionId.value = sendResp.session_id

      // 长轮询等待回复
      const pollResp = await pollReply(sendResp.session_id, 30)
      if (pollResp.has_message) {
        const botMsg: ChatMessage = {
          id: `msg-${++msgCounter}`,
          role: "bot",
          content: pollResp.reply || "抱歉，我暂时无法处理。",
          timestamp: new Date(),
          intent: pollResp.intent,
          confidence: pollResp.confidence,
          isTransfer: pollResp.is_transfer,
        }
        messages.value.push(botMsg)
      }
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
