<template>
  <el-tooltip :content="isDark ? '切换到浅色' : '切换到深色'" placement="bottom">
    <el-button text :icon="isDark ? Moon : Sunny" circle @click="toggle" />
  </el-tooltip>
</template>

<script setup lang="ts">
import { Moon, Sunny } from "@element-plus/icons-vue"
import { useColorMode } from "@/composables/useColorMode"

const { isDark, toggle } = useColorMode()
</script>
