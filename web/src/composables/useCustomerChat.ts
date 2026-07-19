import { ref, onUnmounted } from "vue"

export interface ChatMsg {
  id: string
  role: "customer" | "bot" | "agent" | "system"
  content: string
  time: Date
}

export function useCustomerChat() {
  const sessionId = ref("")
  const messages = ref<ChatMsg[]>([])
  const connected = ref(false)
  const polling = ref(false)
  const inQueue = ref(false)
  const queuePosition = ref(0)
  const agentName = ref("")

  let pollAbort: AbortController | null = null
  let msgCounter = 0

  function addMsg(role: ChatMsg["role"], content: string) {
    messages.value.push({ id: `m${++msgCounter}`, role, content, time: new Date() })
  }

  async function sendMessage(text: string) {
    if (!text.trim()) return
    addMsg("customer", text)

    try {
      const resp = await fetch("/api/chat/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId.value || undefined, message: text }),
      })
      if (!resp.ok) {
        addMsg("system", `服务暂时不可用 (${resp.status})，请稍后重试`)
        return
      }
      const data = await resp.json()
      if (data.session_id) sessionId.value = data.session_id
    } catch {
      addMsg("system", "网络连接失败，请检查网络后重试")
    }
  }

  async function startPolling() {
    if (!sessionId.value) return
    polling.value = true
    pollAbort = new AbortController()

    while (polling.value) {
      try {
        const url = `/api/chat/poll?session_id=${sessionId.value}&timeout=25`
        const resp = await fetch(url, { signal: pollAbort.signal })
        const data = await resp.json()

        if (data.status === "done" && data.reply) {
          addMsg(data.is_transfer ? "system" : "bot", data.reply)
          if (data.is_transfer) {
            addMsg("system", "正在转接人工客服...")
            inQueue.value = true
            // 开始轮询 star-conn
            startStarPolling()
            return
          }
        } else if (data.status === "queued") {
          queuePosition.value = data.position || 0
          inQueue.value = true
        } else if (data.status === "processing") {
          // 等待中
        }
      } catch (e: any) {
        if (e.name === "AbortError") break
        await new Promise(r => setTimeout(r, 1000))
      }
    }
  }

  async function startStarPolling() {
    while (inQueue.value && sessionId.value) {
      try {
        const resp = await fetch(`/api/star/sessions/${sessionId.value}/poll?timeout=25000`)
        if (!resp.ok) { await new Promise(r => setTimeout(r, 1000)); continue }
        const msgs = await resp.json()
        for (const m of msgs || []) {
          const role = m.sender === "agent" ? "agent" as const : "customer" as const
          if (role === "agent") {
            addMsg("agent", m.content)
            connected.value = true
            inQueue.value = false
          }
        }
      } catch { await new Promise(r => setTimeout(r, 1000)) }
    }
  }

  function stopPolling() {
    polling.value = false
    pollAbort?.abort()
    pollAbort = null
  }

  function clearChat() {
    stopPolling()
    sessionId.value = ""
    messages.value = []
    connected.value = false
    inQueue.value = false
    agentName.value = ""
  }

  onUnmounted(() => stopPolling())

  return { sessionId, messages, connected, polling, inQueue, queuePosition, agentName, sendMessage, startPolling, stopPolling, clearChat, addMsg }
}
