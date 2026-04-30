<template>
  <div class="agent-workbench">
    <SessionList />
    <ConversationPanel />
    <AssistPanel />
  </div>
</template>

<script setup lang="ts">
import SessionList from "@/components/assist/SessionList.vue"
import ConversationPanel from "@/components/assist/ConversationPanel.vue"
import AssistPanel from "@/components/assist/AssistPanel.vue"
</script>

<style scoped>
.agent-workbench {
  display: flex;
  height: 100%;
  background: #f0f2f5;
}
</style>
