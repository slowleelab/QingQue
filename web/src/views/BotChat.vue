<template>
  <div class="bot-chat-page">
    <ChatWindow />
  </div>
</template>

<script setup lang="ts">
import ChatWindow from "@/components/chat/ChatWindow.vue"
</script>

<style scoped>
.bot-chat-page {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100%;
  background: #f0f2f5;
  padding: 24px;
}
</style>
