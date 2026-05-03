import { ref, onUnmounted } from "vue"
import type { ChatSendResponse, PollResponse } from "@/api/types"

export type PollMode = "bot" | "agent"

export function useChatPoll() {
  const mode = ref<PollMode>("bot")
  const sessionId = ref<string>("")
  const polling = ref(false)
  const error = ref<string | null>(null)

  let abortController: AbortController | null = null

  async function sendMessage(message: string): Promise<ChatSendResponse> {
    const resp = await fetch("/api/bot/chat/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId.value || undefined,
        message,
      }),
    })
    const data = await resp.json()
    sessionId.value = data.session_id
    return data as ChatSendResponse
  }

  async function startPolling(
    onMessage: (data: PollResponse) => void,
  ) {
    polling.value = true
    abortController = new AbortController()

    while (polling.value && sessionId.value) {
      try {
        const url = `/api/bot/chat/poll?session_id=${sessionId.value}&timeout=25`
        const resp = await fetch(url, { signal: abortController.signal })
        const data = await resp.json()

        if (data.has_message) {
          onMessage(data as PollResponse)
        }
      } catch (e: any) {
        if (e.name !== "AbortError") {
          error.value = e.message
          await new Promise(r => setTimeout(r, 1000))
        }
      }
    }
  }

  function stopPolling() {
    polling.value = false
    abortController?.abort()
    abortController = null
  }

  function switchTo(m: PollMode, sid: string) {
    stopPolling()
    mode.value = m
    sessionId.value = sid
  }

  onUnmounted(() => stopPolling())

  return { mode, sessionId, polling, error, sendMessage, startPolling, stopPolling, switchTo }
}
