<template>
  <div class="chat-window">
    <div class="chat-header">
      <span class="title">Lumio 智能客服</span>
      <el-tag size="small" type="success">在线</el-tag>
      <el-button text size="small" @click="chatStore.clearSession()">新会话</el-button>
    </div>
    <MessageList />
    <ChatInput @send="onSend" :disabled="chatStore.isLoading" :draft-key="chatStore.sessionId ?? ''" />
  </div>
</template>

<script setup lang="ts">
import { useChatStore } from "@/stores/chat"
import { useChat } from "@/composables/useChat"
import MessageList from "./MessageList.vue"
import ChatInput from "./ChatInput.vue"

const chatStore = useChatStore()
const { sendMessage } = useChat()

function onSend(text: string) {
  sendMessage(text)
}
</script>

<style scoped>
.chat-window {
  width: 420px;
  height: 640px;
  display: flex;
  flex-direction: column;
  background: var(--color-bg-surface);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-md);
  overflow: hidden;
}

.chat-header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  background: var(--color-primary);
  color: var(--color-text-on-primary);
}

.chat-header .title {
  flex: 1;
  font-size: var(--fs-lg);
  font-weight: 600;
}

.chat-header .el-button {
  color: var(--color-text-on-primary);
}
</style>
