/** 与后端 models.py 对齐的 TypeScript 类型定义 */

// ── 枚举类型 ──

export type ChannelType = "web" | "app" | "wechat" | "phone"
export type SessionPhase = "bot" | "handoff" | "assist" | "ended"
export type IntentLabel =
  | "faq"
  | "bill_query"
  | "transaction_query"
  | "limit_query"
  | "installment_inquiry"
  | "reward_query"
  | "card_loss"
  | "complaint"
  | "transfer_agent"
  | "chitchat"
export type AlertLevel = "info" | "warning" | "critical"
export type AlertCategory = "compliance" | "emotion" | "silence" | "process"

// ── Bot API ──

export interface ChatRequest {
  session_id?: string
  customer_id?: string
  message: string
  channel?: ChannelType
}

export interface ChatResponse {
  session_id: string
  reply: string
  intent?: IntentLabel
  confidence: number
  source: string
  is_transfer: boolean
}

// ── Assist 推送 ──

export interface ScriptCard {
  script_id: string
  content: string
  tags: string[]
  priority: number
}

export interface KnowledgeSnippet {
  chunk_id: string
  summary: string
  source: string
  confidence: "high" | "medium" | "low"
}

export interface AlertObject {
  level: AlertLevel
  category: AlertCategory
  message: string
  suggestion?: string
}

export interface ProductRecommendation {
  product_id: string
  product_name: string
  reason: string
  script_suggestion: string
  risk_tip: string
  eligibility_match: boolean
}

export interface AssistPushPayload {
  scripts: ScriptCard[]
  knowledge: KnowledgeSnippet[]
  alerts: AlertObject[]
  recommendations: ProductRecommendation[]
}

export interface AssistPushMessage {
  type: string
  session_id: string
  timestamp: string
  trigger: string
  payload: AssistPushPayload
}

// ── 错误响应 ──

export interface ApiError {
  error: {
    code: number
    message: string
    type: string
  }
}

// ── 聊天消息（前端内部） ──

export interface ChatMessage {
  id: string
  role: "customer" | "bot" | "agent"
  content: string
  timestamp: Date
  intent?: IntentLabel
  confidence?: number
  isTransfer?: boolean
}

// ── 会话信息（前端内部） ──

export interface SessionInfo {
  sessionId: string
  phase: SessionPhase
  lastActiveAt: Date
  customerName?: string
}

// ── Long Poll ──

export interface ChatSendResponse {
  accepted: boolean
  message_id: string
  session_id: string
}

export interface PollResponse {
  has_message: boolean
  reply: string
  intent?: IntentLabel
  confidence: number
  source: string
  is_transfer: boolean
  transfer_url: string
  transfer_reason: string
}

export interface CustomerPollResponse {
  has_message: boolean
  messages?: Array<{
    sender: "customer" | "agent" | "system"
    content: string
    timestamp: string
  }>
  session_ended?: boolean
}
