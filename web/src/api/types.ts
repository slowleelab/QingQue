/** 与后端 models.py 对齐的 TypeScript 类型定义 */

// ── 枚举类型 ──

export type ChannelType = "web" | "app" | "wechat" | "phone"
export type SessionPhase = "bot" | "agent" | "ended"
export type SessionSubPhase = "bot:active" | "agent:queued" | "agent:assigned" | "agent:active" | "agent:on_hold" | "agent:reviewing"
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
  agentId?: string
}

// ── Long Poll ──

export interface ChatSendResponse {
  accepted: boolean
  message_id: string
  session_id: string
}

// v2.0: 轮询返回 status 字段替代 has_message
export interface PollResponse {
  status: "done" | "queued" | "processing" | "timeout"
  reply?: string
  intent?: IntentLabel
  confidence?: number
  source?: string
  is_transfer?: boolean
  transfer_url?: string
  transfer_reason?: string
  position?: number
  est_wait?: string
  suggestion?: string
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

// ── KB 文档管理 ──

export interface KbDocument {
  doc_id: string
  title: string
  source_type: string
  category: string
  doc_type: string
  status: string
  chunk_count: number
  created_at: string | null
}

export interface KbDocumentListResponse {
  documents: KbDocument[]
  total: number
}

export interface KbDocumentStatus {
  doc_id: string
  title: string
  status: string
  chunk_count: number
  stages: Array<{ stage: string; status: string; duration_ms: number; error_message?: string }>
}

// ── FAQ 管理 ──

export interface FaqItem {
  id: string
  question: string
  category: string
  approval_status: string
  version: number
  is_current_version: boolean
  card_types: string[]
  effective_date: string | null
  expiry_date: string | null
  created_at: string | null
}

export interface FaqDetail extends FaqItem {
  answer: string
  variant_questions: string[]
  customer_tiers: string[]
  keywords: string[]
  sort_order: number
  doc_group: string
  allowed_roles: string[]
  regulatory_tags: string[]
  created_by: string
  updated_by: string | null
  updated_at: string | null
}

export interface FaqListResponse {
  faqs: FaqItem[]
  total: number
}

export interface FaqCreateRequest {
  question: string
  answer: string
  variant_questions?: string[]
  category: string
  card_types?: string[]
  customer_tiers?: string[]
  keywords?: string[]
  effective_date?: string | null
  expiry_date?: string | null
  allowed_roles?: string[]
  regulatory_tags?: string[]
}

export interface FaqUpdateRequest {
  question?: string | null
  answer?: string | null
  variant_questions?: string[] | null
  category?: string | null
  card_types?: string[] | null
  keywords?: string[] | null
  sort_order?: number | null
}

export interface FaqApprovalResult {
  status: string
  faq_id: string
  approval_status: string
}
