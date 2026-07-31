<template>
  <el-card shadow="hover" class="script-card" data-testid="script-card">
    <div class="priority">
      <el-icon v-for="n in card.priority" :key="n" color="var(--color-warning)"><Star /></el-icon>
    </div>
    <p class="content">{{ card.content }}</p>
    <div class="tags">
      <el-tag v-for="tag in card.tags" :key="tag" size="small" type="info">{{ tag }}</el-tag>
    </div>
    <div class="actions">
      <el-button size="small" type="success" plain @click="$emit('adopt', card.script_id)">
        <el-icon><Check /></el-icon> 采纳
      </el-button>
      <el-button size="small" type="warning" plain @click="$emit('modify', card.script_id)">
        <el-icon><Edit /></el-icon> 修改
      </el-button>
      <el-button size="small" type="info" plain @click="$emit('dismiss', card.script_id)">
        <el-icon><Close /></el-icon> 关闭
      </el-button>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { Star, Check, Edit, Close } from "@element-plus/icons-vue"
import type { ScriptCard as ScriptCardType } from "@/api/types"

defineProps<{ card: ScriptCardType }>()
defineEmits<{
  adopt: [scriptId: string]
  modify: [scriptId: string]
  dismiss: [scriptId: string]
}>()
</script>

<style scoped>
.script-card { margin-bottom: 10px; }
.priority { margin-bottom: 6px; }
.content { font-size: var(--fs-base); line-height: 1.6; color: var(--color-text-primary); margin-bottom: var(--space-2); }
.tags { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: var(--space-2); }
.actions { display: flex; gap: 6px; margin-top: var(--space-2); padding-top: var(--space-2); border-top: 1px solid var(--color-border-lighter); }
</style>
