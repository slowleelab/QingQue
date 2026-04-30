<template>
  <el-card shadow="hover" class="knowledge-card">
    <p class="summary">{{ snippet.summary }}</p>
    <div class="meta">
      <el-tag size="small">{{ snippet.source }}</el-tag>
      <el-tag :type="confidenceType" size="small" effect="plain">
        {{ snippet.confidence === "high" ? "高置信" : snippet.confidence === "medium" ? "中置信" : "低置信" }}
      </el-tag>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from "vue"
import type { KnowledgeSnippet as KnowledgeSnippetType } from "@/api/types"

const props = defineProps<{ snippet: KnowledgeSnippetType }>()

const confidenceType = computed(() => {
  const map: Record<string, "" | "warning" | "info" | "success"> = { high: "success", medium: "warning", low: "info" }
  return (map[props.snippet.confidence] ?? "info") as "" | "warning" | "info" | "success"
})
</script>

<style scoped>
.knowledge-card {
  margin-bottom: 10px;
}

.summary {
  font-size: 13px;
  line-height: 1.6;
  color: #606266;
  margin-bottom: 8px;
}

.meta {
  display: flex;
  gap: 6px;
}
</style>
