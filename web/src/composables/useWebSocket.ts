import { ref, watch, onScopeDispose, type Ref } from "vue"
import { useAssistStore } from "@/stores/assist"

export type WsStatus = "connecting" | "connected" | "disconnected" | "error"

export function useWebSocket(sessionId: Ref<string | null>) {
  const status = ref<WsStatus>("disconnected")
  let ws: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null
  let reconnectDelay = 1000

  const assistStore = useAssistStore()

  function getWsUrl(sid: string): string {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
    return `${protocol}//${window.location.host}/api/assist/ws/${sid}`
  }

  function connect() {
    if (!sessionId.value) return
    disconnect()

    status.value = "connecting"
    assistStore.setWsStatus("connecting")

    ws = new WebSocket(getWsUrl(sessionId.value))

    ws.onopen = () => {
      status.value = "connected"
      assistStore.setWsStatus("connected")
      reconnectDelay = 1000
      startHeartbeat()
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        assistStore.onPushMessage(msg)
      } catch {
        // 非 JSON 消息，忽略
      }
    }

    ws.onclose = (event) => {
      stopHeartbeat()
      if (!event.wasClean) {
        scheduleReconnect()
      } else {
        status.value = "disconnected"
        assistStore.setWsStatus("disconnected")
      }
    }

    ws.onerror = () => {
      status.value = "error"
      assistStore.setWsStatus("error")
    }
  }

  function disconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    stopHeartbeat()
    if (ws) {
      ws.onclose = null
      ws.close()
      ws = null
    }
    status.value = "disconnected"
    assistStore.setWsStatus("disconnected")
  }

  function scheduleReconnect() {
    status.value = "disconnected"
    assistStore.setWsStatus("disconnected")
    reconnectTimer = setTimeout(() => {
      reconnectDelay = Math.min(reconnectDelay * 2, 30000)
      connect()
    }, reconnectDelay)
  }

  function startHeartbeat() {
    heartbeatTimer = setInterval(() => {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send("ping")
      }
    }, 30000)
  }

  function stopHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
  }

  // 当 sessionId 变化时自动重连（停止 watch 以便清理）
  const stopWatch = watch(sessionId, (newId, oldId) => {
    if (newId !== oldId) {
      connect()
    }
  })

  // 组件卸载时自动清理
  onScopeDispose(() => {
    stopWatch()
    disconnect()
  })

  function send(data: Record<string, unknown>) {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(data))
    }
  }

  return { status, connect, disconnect, send }
}
