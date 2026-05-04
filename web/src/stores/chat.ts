import { defineStore } from "pinia"
import { ref } from "vue"
import { sendMessage, pollReply } from "@/api/bot"
import type { ChatMessage, ChatRequest } from "@/api/types"

export const useChatStore = defineStore("chat", () => {
  const messages = ref<ChatMessage[]>([])
  const sessionId = ref<string | null>(null)
  const isLoading = ref(false)
  const transferUrl = ref<string | null>(null)  // star-connection 轮询地址
  const agentConnected = ref(false)

  let msgCounter = 0
  let agentPollTimer: ReturnType<typeof setInterval> | null = null

  async function send(text: string) {
    const userMsg: ChatMessage = {
      id: `msg-${++msgCounter}`,
      role: "customer" as const,
      content: text,
      timestamp: new Date(),
    }
    messages.value.push(userMsg)
    isLoading.value = true

    try {
      // 如果已转人工，发消息到 star-connection
      if (transferUrl.value) {
        const sid = transferUrl.value.match(/session_id=([^&]+)/)?.[1]
        if (sid) {
          await fetch("/api/star/sessions/" + sid + "/messages", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sender: "customer", content: text }),
          })
        }
        isLoading.value = false
        return
      }

      // Bot 阶段：发送 + 轮询
      const request: ChatRequest = {
        message: text,
        session_id: sessionId.value ?? undefined,
      }
      const sendResp = await sendMessage(request)
      sessionId.value = sendResp.session_id

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

        // 转人工：记录 transfer_url，开始轮询 star-connection
        if (pollResp.is_transfer && pollResp.transfer_url) {
          transferUrl.value = pollResp.transfer_url
          agentConnected.value = true
          startAgentPolling()
        }
      }
    } catch {
      // silent
    } finally {
      isLoading.value = false
    }
  }

  function startAgentPolling() {
    if (agentPollTimer) clearInterval(agentPollTimer)
    agentPollTimer = setInterval(async () => {
      if (!transferUrl.value) return
      try {
        // 用相对路径，Vite 代理到 star-connection
        const sid = transferUrl.value.match(/session_id=([^&]+)/)?.[1]
        if (!sid) return
        const resp = await fetch("/api/star/sessions/" + sid + "/messages")
        if (!resp.ok) return
        const msgs: Array<{ sender: string; content: string; messageId: string; timestamp: number }> = await resp.json()
        for (const m of msgs) {
          if (m.sender === "agent") {
            const exists = messages.value.some(existing => existing.id === m.messageId)
            if (!exists) {
              messages.value.push({
                id: m.messageId,
                role: "agent",
                content: m.content,
                timestamp: new Date(m.timestamp),
              })
            }
          }
        }
      } catch { /* silent */ }
    }, 2000)
  }

  function clearSession() {
    messages.value = []
    sessionId.value = null
    transferUrl.value = null
    agentConnected.value = false
    if (agentPollTimer) clearInterval(agentPollTimer)
    agentPollTimer = null
  }

  return { messages, sessionId, isLoading, transferUrl, agentConnected, send, clearSession }
})
