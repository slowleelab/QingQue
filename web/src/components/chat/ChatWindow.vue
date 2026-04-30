<template>
  <div class="chat-window">
    <div class="chat-header">
      <span class="title">SmartCS 智能客服</span>
      <el-tag size="small" type="success">在线</el-tag>
      <el-button text size="small" @click="chatStore.clearSession()">新会话</el-button>
    </div>
    <MessageList />
    <ChatInput @send="onSend" :disabled="chatStore.isLoading" />
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
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
  overflow: hidden;
}

.chat-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  background: var(--color-primary);
  color: #fff;
}

.chat-header .title {
  flex: 1;
  font-size: 16px;
  font-weight: 600;
}

.chat-header .el-button {
  color: #fff;
}
</style>
