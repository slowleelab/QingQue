<template>
  <div class="message-bubble" :class="[message.role]">
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
  const map: Record<string, string> = { customer: "#409eff", bot: "#67c23a", agent: "#e6a23c" }
  return { background: map[props.message.role] ?? "#909399" }
})

function formatTime(date: Date): string {
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
}
</script>

<style scoped>
.message-bubble {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}

.message-bubble.agent {
  flex-direction: row-reverse;
}

.message-bubble.agent .bubble-body {
  align-items: flex-end;
}

.message-bubble.customer .bubble-content {
  background: #fff;
  color: #303133;
  border: 1px solid #e4e7ed;
  border-radius: 4px 12px 12px 12px;
}

.message-bubble.bot .bubble-content {
  background: #f0f9eb;
  color: #303133;
  border-radius: 4px 12px 12px 12px;
}

.message-bubble.agent .bubble-content {
  background: #ecf5ff;
  color: #303133;
  border-radius: 12px 4px 12px 12px;
}

.bubble-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-width: 320px;
}

.bubble-content {
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.5;
  word-break: break-word;
}

.bubble-meta {
  display: flex;
  align-items: center;
  gap: 6px;
}

.confidence {
  font-size: 11px;
  color: #909399;
}

.transfer-tip {
  margin-top: 2px;
}

.bubble-time {
  font-size: 11px;
  color: #c0c4cc;
}
</style>
