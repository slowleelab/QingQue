import { client } from "./client"
import type {
  KbDocumentListResponse,
  KbDocumentStatus,
  FaqListResponse,
  FaqDetail,
  FaqCreateRequest,
  FaqUpdateRequest,
  FaqApprovalResult,
} from "./types"

// ── 文档管理 ──

export function listDocuments(params?: {
  category?: string
  limit?: number
  offset?: number
}): Promise<KbDocumentListResponse> {
  return client.get("/kb/documents", { params })
}

export function uploadDocument(
  file: File,
  category: string,
  docType: string,
): Promise<{ doc_id: string }> {
  const form = new FormData()
  form.append("file", file)
  form.append("category", category)
  form.append("doc_type", docType)
  return client.post("/kb/documents", form, {
    headers: { "Content-Type": "multipart/form-data" },
  })
}

export function deleteDocument(docId: string): Promise<{ status: string; doc_id: string }> {
  return client.delete(`/kb/documents/${docId}`)
}

export function getDocumentStatus(docId: string): Promise<KbDocumentStatus> {
  return client.get(`/kb/documents/${docId}/status`)
}

/** 重新摄入失败文档 (kb-service 后端: POST /api/v1/documents/{id}/reindex) */
export function retryIngestion(docId: string): Promise<{ doc_id: string; reindexed: boolean; total: number }> {
  return client.post(`/kb/documents/${docId}/reindex`)
}

// ── FAQ CRUD ──

export function listFaqs(params?: {
  category?: string
  approval_status?: string
  limit?: number
  offset?: number
}): Promise<FaqListResponse> {
  return client.get("/kb/faq", { params })
}

export function getFaq(faqId: string): Promise<FaqDetail> {
  return client.get(`/kb/faq/${faqId}`)
}

export function createFaq(data: FaqCreateRequest): Promise<{ faq_id: string; approval_status: string }> {
  return client.post("/kb/faq", data)
}

export function updateFaq(faqId: string, data: FaqUpdateRequest): Promise<{ status: string }> {
  return client.put(`/kb/faq/${faqId}`, data)
}

export function deleteFaq(faqId: string): Promise<{ status: string }> {
  return client.delete(`/kb/faq/${faqId}`)
}

// ── FAQ 审批 ──

export function submitFaq(faqId: string, comment = ""): Promise<FaqApprovalResult> {
  return client.post(`/kb/faq/${faqId}/submit`, { comment })
}

export function approveFaq(faqId: string, comment = ""): Promise<FaqApprovalResult> {
  return client.post(`/kb/faq/${faqId}/approve`, { comment })
}

export function rejectFaq(faqId: string, comment = ""): Promise<FaqApprovalResult> {
  return client.post(`/kb/faq/${faqId}/reject`, { comment })
}

export function publishFaq(faqId: string, comment = ""): Promise<FaqApprovalResult> {
  return client.post(`/kb/faq/${faqId}/publish`, { comment })
}

export function archiveFaq(faqId: string, comment = ""): Promise<FaqApprovalResult> {
  return client.post(`/kb/faq/${faqId}/archive`, { comment })
}
