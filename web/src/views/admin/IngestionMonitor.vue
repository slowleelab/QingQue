<template>
  <div class="monitor-page">
    <div class="page-header">
      <h2>摄入管道监控</h2>
      <el-button @click="load" :loading="loading">刷新</el-button>
    </div>

    <el-table :data="documents" v-loading="loading" stripe style="margin-top: 16px">
      <el-table-column prop="title" label="文档" min-width="200" show-overflow-tooltip />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="chunk_count" label="分块" width="70" align="center" />
      <el-table-column prop="created_at" label="上传时间" width="170">
        <template #default="{ row }">{{ row.created_at?.slice(0, 16).replace("T", " ") || "-" }}</template>
      </el-table-column>
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="viewDetail(row)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="detailVisible" title="摄入详情" width="520px">
      <div v-if="detail">
        <p><strong>文档：</strong>{{ detail.title }}</p>
        <p><strong>状态：</strong><el-tag :type="statusType(detail.status)">{{ statusText(detail.status) }}</el-tag></p>
        <el-table :data="detail.stages || []" size="small" style="margin-top: 12px">
          <el-table-column prop="stage" label="阶段" />
          <el-table-column prop="status" label="状态" width="100">
            <template #default="{ row: s }">
              <el-tag :type="rowStatusTag(s.status)" size="small">{{ s.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="duration_ms" label="耗时(ms)" width="100" align="right" />
          <el-table-column prop="error_message" label="错误" min-width="150" show-overflow-tooltip />
        </el-table>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue"
import { listDocuments, getDocumentStatus } from "@/api/admin"
import type { KbDocument, KbDocumentStatus } from "@/api/types"

const documents = ref<KbDocument[]>([])
const loading = ref(false)
const detailVisible = ref(false)
const detail = ref<KbDocumentStatus | null>(null)

async function load() {
  loading.value = true
  try {
    const res = await listDocuments({ limit: 50 })
    documents.value = res.documents
  } catch {
    // handled
  } finally {
    loading.value = false
  }
}

async function viewDetail(row: KbDocument) {
  try {
    detail.value = await getDocumentStatus(row.doc_id)
    detailVisible.value = true
  } catch {
    // handled
  }
}

function statusType(s: string) {
  const m: Record<string, string> = { ingested: "success", ingesting: "warning", failed: "danger", pending: "info" }
  return m[s] ?? "info"
}
function statusText(s: string) {
  const m: Record<string, string> = { ingested: "已就绪", ingesting: "摄入中", failed: "失败", pending: "待处理" }
  return m[s] ?? s
}
function rowStatusTag(s: string) {
  return s === "completed" ? "success" : s === "failed" ? "danger" : "info"
}

load()
</script>

<style scoped>
.monitor-page { max-width: 1200px; }
.page-header { display: flex; align-items: center; justify-content: space-between; }
.page-header h2 { margin: 0; font-size: 20px; }
</style>
