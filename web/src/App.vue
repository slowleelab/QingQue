<template>
  <router-view />
  <CommandPalette v-model="paletteOpen" />
</template>

<script setup lang="ts">
import { ref } from "vue"
import CommandPalette from "@/components/common/CommandPalette.vue"
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
