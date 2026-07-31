/** chat-svc (Java) HTTP API 包装
 *
 * 修复 audit item 22: 之前 6 处裸 fetch('/api/chat-svc/...') 绕过 axios
 * 拦截器, 401 不跳登录, 4xx/5xx 静默吞错. 本文件统一走 client (axios),
 * 自动 Bearer + 错误拦截.
 *
 * 所有函数返回 typed Promise. 长轮询函数 (pollChatSvcMessages) 走原始 fetch
 * 因为它的超时语义 (timeout=25000ms) 与 axios 默认不一致, 但仍带 Bearer 头.
 */

import { client, getToken } from "./client"

// ── 类型 (与后端 chat-svc 对齐) ──

export interface ChatSvcSessionRaw {
  sessionId: string
  status: "ACTIVE" | "WAITING" | "CLOSED" | string
  agentId: string | null
  createTime: number  // epoch ms
  customerId: string | null
}

export interface ChatSvcMessageRaw {
  sender: "customer" | "agent" | "system" | string
  content: string
  messageId: string
  timestamp: number  // epoch ms
}

// ── 会话 ──

/** 列出 chat-svc 全部活跃会话 (监控页用) */
export async function listChatSvcSessions(): Promise<ChatSvcSessionRaw[]> {
  return (await client.get<ChatSvcSessionRaw[]>("/chat-svc/monitor/customer-service/sessions")) as unknown as ChatSvcSessionRaw[]
}

/** 创建 chat-svc 会话 (转人工入口) */
export async function createChatSvcSession(payload: {
  session_id: string
  customer_id?: string
  transfer_reason?: string
  transfer_summary?: string
  history?: Array<{ role: string; content: string }>
  intent?: string
  sentiment?: string
}): Promise<{ session_id: string; status: string }> {
  return (await client.post("/chat-svc/sessions", payload)) as unknown as { session_id: string; status: string }
}

/** 发送消息到 chat-svc (坐席侧 POST /sessions/{sid}/messages) */
export async function sendChatSvcMessage(
  sid: string,
  body: { sender: "customer" | "agent"; content: string },
): Promise<{ messageId: string; timestamp: number }> {
  return (await client.post(`/chat-svc/sessions/${encodeURIComponent(sid)}/messages`, body)) as unknown as { messageId: string; timestamp: number }
}

// ── 长轮询 (单独走 fetch, 因为 axios 不支持 25s 长 timeout 与 SSE 风格) ──

/** 长轮询新消息. 返回消息数组, 调用方负责维护 since 游标.
 *
 * 仍然带 Bearer 头, 但 4xx/5xx 不再静默吞 — 由 B1 usePolling 的 onError 接管.
 * 401 走 axios 拦截器跳登录 (本函数手工抛 401 让外层处理).
 */
export async function pollChatSvcMessages(
  sid: string,
  since: number,
  timeoutMs = 25000,
): Promise<ChatSvcMessageRaw[]> {
  const token = getToken()
  const url = `/api/chat-svc/sessions/${encodeURIComponent(sid)}/poll?timeout=${timeoutMs}&since=${since}`
  const resp = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (resp.status === 401) {
    // 与 axios 拦截器保持一致: 跳登录
    localStorage.removeItem("lumio_token")
    if (window.location.pathname !== "/login") {
      window.location.href = "/login"
    }
    throw new Error("未登录")
  }
  if (!resp.ok) throw new Error(`poll HTTP ${resp.status}`)
  return (await resp.json()) as ChatSvcMessageRaw[]
}
