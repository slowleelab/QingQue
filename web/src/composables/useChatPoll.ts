import { ref, onUnmounted } from "vue"
import type { ChatSendResponse, PollResponse } from "@/api/types"

export type PollMode = "bot" | "agent"

/**
 * Bot 长轮询 — v2.0 适配新状态格式。
 *
 * 轮询返回 status 字段: done / queued / processing / timeout
 * 前端根据状态展示排队提示或处理结果。
 */
export function useChatPoll() {
  const mode = ref<PollMode>("bot")
  const sessionId = ref<string>("")
  const polling = ref(false)
  const error = ref<string | null>(null)
  const pollStatus = ref<string>("")

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
    onStatus?: (status: string, data?: Record<string, unknown>) => void,
  ) {
    polling.value = true
    abortController = new AbortController()

    while (polling.value && sessionId.value) {
      try {
        const url = `/api/bot/chat/poll?session_id=${sessionId.value}&timeout=25`
        const resp = await fetch(url, { signal: abortController.signal })
        const data = await resp.json()

        pollStatus.value = data.status || ""

        switch (data.status) {
          case "done":
            onMessage(data as PollResponse)
            stopPolling()
            return
          case "processing":
            onStatus?.("processing", data)
            break
          case "queued":
            onStatus?.("queued", data)
            break
          case "timeout":
            onStatus?.("timeout", data)
            break
          default:
            // 兼容旧格式: has_message
            if (data.has_message || data.reply) {
              onMessage(data as PollResponse)
              stopPolling()
              return
            }
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

  return { mode, sessionId, polling, error, pollStatus, sendMessage, startPolling, stopPolling, switchTo }
}
