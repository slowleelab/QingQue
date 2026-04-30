<template>
  <el-alert :type="alertType" :title="alert.message" :description="alert.suggestion" show-icon :closable="false" />
</template>

<script setup lang="ts">
import { computed } from "vue"
import type { AlertObject, AlertLevel } from "@/api/types"

const props = defineProps<{ alert: AlertObject }>()

const levelMap: Record<AlertLevel, "" | "warning" | "error"> = {
  info: "",
  warning: "warning",
  critical: "error",
}

const alertType = computed(() => levelMap[props.alert.level])
</script>

<style scoped>
.el-alert {
  margin-bottom: 8px;
}
</style>
