import { client } from "./client"
import type { ChatRequest, ChatResponse } from "./types"

export function sendMessage(request: ChatRequest): Promise<ChatResponse> {
  return client.post("/bot/chat", request)
}
