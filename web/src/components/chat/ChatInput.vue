<template>
  <div class="chat-input">
    <el-input
      v-model="text"
      type="textarea"
      :autosize="{ minRows: 1, maxRows: 4 }"
      placeholder="请输入您的问题..."
      :disabled="disabled"
      @keydown.enter.exact.prevent="handleSend"
    />
    <el-button
      type="primary"
      :icon="Promotion"
      circle
      :disabled="!text.trim() || disabled"
      @click="handleSend"
    />
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue"
import { Promotion } from "@element-plus/icons-vue"

defineProps<{ disabled: boolean }>()
const emit = defineEmits<{ send: [text: string] }>()

const text = ref("")

function handleSend() {
  if (!text.value.trim()) return
  emit("send", text.value)
  text.value = ""
}
</script>

<style scoped>
.chat-input {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid #ebeef5;
  background: #fff;
}

.chat-input :deep(.el-textarea__inner) {
  resize: none;
  border-radius: 8px;
}
</style>
