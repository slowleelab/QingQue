import { client } from "./client"
import type { ChatRequest, ChatSendResponse, PollResponse } from "./types"

export function sendMessage(request: ChatRequest): Promise<ChatSendResponse> {
  return client.post("/bot/chat/send", request)
}

export function pollReply(sessionId: string, timeout = 25): Promise<PollResponse> {
  return client.get("/bot/chat/poll", { params: { session_id: sessionId, timeout } })
}
