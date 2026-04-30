import { nextTick, type Ref } from "vue"
import { useChatStore } from "@/stores/chat"

export function useChat(messageListRef?: Ref<HTMLElement | null>) {
  const store = useChatStore()

  async function sendMessage(text: string) {
    if (!text.trim() || store.isLoading) return
    await store.send(text.trim())
    await scrollToBottom()
  }

  async function scrollToBottom() {
    await nextTick()
    const container = messageListRef?.value ?? document.querySelector(".message-list")
    if (container) {
      container.scrollTop = container.scrollHeight
    }
  }

  return { sendMessage, scrollToBottom }
}
