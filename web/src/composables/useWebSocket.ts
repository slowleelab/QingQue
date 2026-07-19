import { ref, onScopeDispose, type Ref } from "vue"
import { useAssistStore } from "@/stores/assist"

export type WsStatus = "connecting" | "connected" | "disconnected" | "error"

/**
 * Assist WebSocket — per-agent 持久连接。
 *
 * v2.0 变更: 从按会话建连改为按坐席建连。
 * - 坐席登录时 connect(agentId)，生命周期 = 上班周期
 * - 会话上下文由消息中 session_id 字段区分
 * - 坐席接受会话时 send({type:"session_activated", session_id})
 */
export function useWebSocket(agentId: Ref<string | null>) {
  const status = ref<WsStatus>("disconnected")
  let ws: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null
  let reconnectDelay = 1000

  const assistStore = useAssistStore()

  function getWsUrl(aid: string): string {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
    return `${protocol}//${window.location.host}/api/assist/ws/agent/${aid}`
  }

  function connect() {
    if (!agentId.value) return
    disconnect()

    status.value = "connecting"
    assistStore.setWsStatus("connecting")

    ws = new WebSocket(getWsUrl(agentId.value))

    ws.onopen = () => {
      status.value = "connected"
      assistStore.setWsStatus("connected")
      reconnectDelay = 1000
      startHeartbeat()
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        // "connected" 消息: 服务端确认连接
        if (msg.type === "connected") {
          status.value = "connected"
          return
        }
        // 其他消息路由到 assist store (通过 session_id 区分)
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
        ws.send(JSON.stringify({ type: "ping" }))
      }
    }, 30000)
  }

  function stopHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
  }

  // 组件卸载时自动清理
  onScopeDispose(() => {
    disconnect()
  })

  function send(data: Record<string, unknown>) {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(data))
    }
  }

  /** 坐席接受会话时调用，通知 Assist 激活该会话 */
  function activateSession(sessionId: string) {
    send({ type: "session_activated", session_id: sessionId })
  }

  /** 坐席发送回复时调用，通知 Assist 做合规检测 */
  function notifyAgentMessage(sessionId: string, content: string) {
    send({ type: "agent_message", session_id: sessionId, content })
  }

  return { status, connect, disconnect, send, activateSession, notifyAgentMessage }
}
