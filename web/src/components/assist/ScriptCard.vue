<template>
  <el-card shadow="hover" class="script-card">
    <div class="priority">
      <el-icon v-for="n in card.priority" :key="n" color="#e6a23c"><Star /></el-icon>
    </div>
    <p class="content">{{ card.content }}</p>
    <div class="tags">
      <el-tag v-for="tag in card.tags" :key="tag" size="small" type="info">{{ tag }}</el-tag>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { Star } from "@element-plus/icons-vue"
import type { ScriptCard as ScriptCardType } from "@/api/types"

defineProps<{ card: ScriptCardType }>()
</script>

<style scoped>
.script-card {
  margin-bottom: 10px;
}

.priority {
  margin-bottom: 6px;
}

.content {
  font-size: 14px;
  line-height: 1.6;
  color: #303133;
  margin-bottom: 8px;
}

.tags {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
</style>
