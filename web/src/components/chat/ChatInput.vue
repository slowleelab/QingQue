<template>
  <div class="chat-input" data-testid="chat-input">
    <el-input
      v-model="text"
      type="textarea"
      :autosize="{ minRows: 1, maxRows: 4 }"
      :placeholder="placeholder"
      :disabled="disabled"
      @keydown.enter.exact.prevent="handleSend"
    />
    <el-button
      data-testid="send-button"
      type="primary"
      :icon="Promotion"
      circle
      :disabled="!text.trim() || disabled || sending"
      :loading="sending"
      @click="handleSend"
    />
  </div>
</template>

<script setup lang="ts">
import { Promotion } from "@element-plus/icons-vue"
import { useDraftText } from "@/composables/useDraftText"

const props = withDefaults(
  defineProps<{
    disabled?: boolean
    sending?: boolean
    /** 草稿持久化 key (建议传 sessionId, 切会话互不干扰) */
    draftKey?: string
    placeholder?: string
  }>(),
  { disabled: false, sending: false, draftKey: "", placeholder: "请输入您的问题... (Enter 发送 · ⌘K 命令面板)" },
)

const emit = defineEmits<{ send: [text: string] }>()

const { text, commit } = useDraftText(`draft:chat:${props.draftKey || "default"}`)

function handleSend() {
  const v = text.value.trim()
  if (!v) return
  emit("send", v)
  commit()
}
</script>

<style scoped>
.chat-input {
  display: flex;
  align-items: flex-end;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid var(--color-border-lighter);
  background: var(--color-bg-surface);
}

.chat-input :deep(.el-textarea__inner) {
  resize: none;
  border-radius: var(--radius-lg);
}
</style>
