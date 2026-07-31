<template>
  <router-view />
  <CommandPalette v-model="paletteOpen" />
  <div class="floating-actions">
    <ThemeToggle />
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue"
import CommandPalette from "@/components/common/CommandPalette.vue"
import ThemeToggle from "@/components/common/ThemeToggle.vue"
import { useShortcuts, registerShortcut } from "@/composables/useShortcuts"

const paletteOpen = ref(false)

useShortcuts()

// ⌘K / Ctrl+K: 打开命令面板
registerShortcut({
  key: "k",
  cmdOrCtrl: true,
  handler: () => { paletteOpen.value = true },
})

// Esc: 关闭命令面板 (优先于全局 dialog 处理)
registerShortcut({
  key: "escape",
  handler: () => {
    if (paletteOpen.value) paletteOpen.value = false
  },
})
</script>

<style scoped>
.floating-actions {
  position: fixed;
  bottom: 20px;
  right: 20px;
  z-index: 100;
  background: var(--color-bg-surface);
  border-radius: var(--radius-full);
  box-shadow: var(--shadow-md);
  padding: 4px;
}
</style>
