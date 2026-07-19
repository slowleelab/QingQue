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
  const seenMessageIds = new Set<string>()

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
          // 更新游标：避免轮询拉回自己刚发的消息
          lastAgentTimestamp = Date.now()
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
      if (pollResp.status === "done") {
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

  let agentPollActive = false
  let lastAgentTimestamp = 0  // 游标：只拉取该时间戳之后的消息

  async function startAgentPolling() {
    if (agentPollActive) return
    agentPollActive = true
    while (agentPollActive && transferUrl.value) {
      try {
        const sid = transferUrl.value.match(/session_id=([^&]+)/)?.[1]
        if (!sid) break
        const url = `/api/star/sessions/${sid}/poll?timeout=25000&since=${lastAgentTimestamp}`
        const resp = await fetch(url)
        if (!resp.ok) { await new Promise(r => setTimeout(r, 1000)); continue }
        const msgs: Array<{ sender: string; content: string; messageId: string; timestamp: number }> = await resp.json()
        for (const m of msgs) {
          if (m.timestamp > lastAgentTimestamp) {
            lastAgentTimestamp = m.timestamp
          }
          if (m.sender === "agent" && !seenMessageIds.has(m.messageId)) {
            seenMessageIds.add(m.messageId)
            messages.value.push({
              id: m.messageId,
              role: "agent",
              content: m.content,
              timestamp: new Date(m.timestamp),
            })
          }
        }
        // 有消息立即继续轮询，无消息等 500ms 避免空转
      } catch { await new Promise(r => setTimeout(r, 1000)) }
    }
  }

  function clearSession() {
    messages.value = []
    sessionId.value = null
    transferUrl.value = null
    agentConnected.value = false
    agentPollActive = false
    lastAgentTimestamp = 0
    seenMessageIds.clear()
  }

  return { messages, sessionId, isLoading, transferUrl, agentConnected, send, clearSession }
})
