<template>
  <div class="message-list">
    <div v-if="chatStore.messages.length === 0" class="empty-tip">
      <el-icon :size="48" color="#c0c4cc"><ChatDotRound /></el-icon>
      <p>您好，请问有什么可以帮您？</p>
    </div>
    <MessageBubble
      v-for="msg in chatStore.messages"
      :key="msg.id"
      :message="msg"
    />
    <div v-if="chatStore.isLoading" class="typing-indicator">
      <span>正在输入</span>
      <span class="dots">...</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ChatDotRound } from "@element-plus/icons-vue"
import { useChatStore } from "@/stores/chat"
import MessageBubble from "./MessageBubble.vue"

const chatStore = useChatStore()
</script>

<style scoped>
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  background: #f9f9f9;
}

.empty-tip {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #909399;
  gap: 12px;
}

.typing-indicator {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 0;
  color: #909399;
  font-size: 13px;
}

.dots {
  animation: blink 1.4s infinite;
}

@keyframes blink {
  0%,
  20% {
    opacity: 0.2;
  }
  50% {
    opacity: 1;
  }
  80%,
  100% {
    opacity: 0.2;
  }
}
</style>
