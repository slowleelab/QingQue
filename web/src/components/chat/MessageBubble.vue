<template>
  <div :data-testid="'message-' + message.role" class="message-bubble" :class="[message.role]">
    <div class="avatar">
      <el-avatar :size="32" :style="avatarStyle">
        {{ avatarText }}
      </el-avatar>
    </div>
    <div class="bubble-body">
      <div class="bubble-content">{{ message.content }}</div>
      <div v-if="message.intent" class="bubble-meta">
        <el-tag size="small" type="info">{{ message.intent }}</el-tag>
        <span v-if="message.confidence" class="confidence">
          置信度 {{ (message.confidence * 100).toFixed(0) }}%
        </span>
      </div>
      <div v-if="message.isTransfer" class="transfer-tip">
        <el-tag size="small" type="warning">即将转接人工坐席</el-tag>
      </div>
      <div class="bubble-time">{{ formatTime(message.timestamp) }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue"
import type { ChatMessage } from "@/api/types"

const props = defineProps<{ message: ChatMessage }>()

const avatarText = computed(() => {
  const map: Record<string, string> = { customer: "客", bot: "AI", agent: "我" }
  return map[props.message.role] ?? "?"
})

const avatarStyle = computed(() => {
  const map: Record<string, string> = {
    customer: "var(--color-primary)",
    bot:      "var(--color-success)",
    agent:    "var(--color-warning)",
  }
  return { background: map[props.message.role] ?? "var(--color-info)", color: "var(--color-text-on-primary)" }
})

function formatTime(date: Date): string {
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
}
</script>

<style scoped>
.message-bubble {
  display: flex;
  gap: var(--space-2);
  margin-bottom: var(--space-4);
}

.message-bubble.agent {
  flex-direction: row-reverse;
}

.message-bubble.agent .bubble-body {
  align-items: flex-end;
}

.message-bubble.customer .bubble-content {
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-sm) var(--radius-xl) var(--radius-xl) var(--radius-xl);
}

.message-bubble.bot .bubble-content {
  background: var(--color-bot-bg);
  color: var(--color-bot-text);
  border-radius: var(--radius-sm) var(--radius-xl) var(--radius-xl) var(--radius-xl);
}

.message-bubble.agent .bubble-content {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
  border-radius: var(--radius-xl) var(--radius-sm) var(--radius-xl) var(--radius-xl);
}

.bubble-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  max-width: 320px;
}

.bubble-content {
  padding: 10px 14px;
  font-size: var(--fs-base);
  line-height: 1.5;
  word-break: break-word;
}

.bubble-meta {
  display: flex;
  align-items: center;
  gap: 6px;
}

.confidence {
  font-size: var(--fs-xs);
  color: var(--color-text-secondary);
}

.transfer-tip {
  margin-top: 2px;
}

.bubble-time {
  font-size: var(--fs-xs);
  color: var(--color-text-placeholder);
}
</style>
