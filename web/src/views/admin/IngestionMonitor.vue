<template>
  <div class="monitor-page">
    <div class="page-header">
      <h2>摄入管道监控</h2>
      <div class="header-controls">
        <el-badge
          v-if="ingestingCount > 0"
          :value="ingestingCount"
          class="ingesting-badge"
          type="warning"
        >
          <el-tag type="warning" size="small">摄入中</el-tag>
        </el-badge>
        <el-switch
          v-model="autoRefresh"
          active-text="自动刷新"
          inline-prompt
          @change="onAutoToggle"
        />
        <el-select v-model="filterCategory" placeholder="分类" clearable size="small" style="width: 140px" @change="load">
          <el-option label="全部" value="" />
          <el-option v-for="c in categoryOptions" :key="c" :label="c" :value="c" />
        </el-select>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <el-table :data="documents" v-loading="loading" stripe style="margin-top: 16px" data-testid="ingestion-table">
      <el-table-column prop="title" label="文档" min-width="200" show-overflow-tooltip />
      <el-table-column label="状态" width="140">
        <template #default="{ row }">
          <div class="status-cell">
            <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
            <el-progress
              v-if="row.status === 'ingesting' && row.progress != null"
              :percentage="row.progress"
              :stroke-width="6"
              :show-text="false"
              style="margin-top: 4px"
            />
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="chunk_count" label="分块" width="70" align="center" />
      <el-table-column prop="created_at" label="上传时间" width="170">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="viewDetail(row)">详情</el-button>
          <el-popconfirm
            v-if="row.status === 'failed'"
            title="确认重新摄入此文档？"
            confirm-button-text="重试"
            @confirm="onRetry(row)"
          >
            <template #reference>
              <el-button link type="warning" size="small" data-testid="retry-btn">重试</el-button>
            </template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="detailVisible" title="摄入详情" width="600px" data-testid="ingestion-detail">
      <div v-if="detail">
        <p><strong>文档：</strong>{{ detail.title }}</p>
        <p><strong>状态：</strong><el-tag :type="statusType(detail.status)">{{ statusText(detail.status) }}</el-tag></p>
        <el-table :data="detail.stages || []" size="small" style="margin-top: 12px">
          <el-table-column prop="stage" label="阶段" width="120" />
          <el-table-column label="状态" width="100">
            <template #default="{ row: s }">
              <el-tag :type="rowStatusTag(s.status)" size="small">{{ s.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="耗时" width="80" align="right">
            <template #default="{ row: s }">{{ s.duration_ms }}ms</template>
          </el-table-column>
          <el-table-column label="错误" min-width="220">
            <template #default="{ row: s }">
              <span v-if="!s.error_message" class="muted">-</span>
              <div v-else class="err-cell">
                <el-link
                  type="danger"
                  :underline="false"
                  @click="copyError(s.error_message!)"
                >复制</el-link>
                <el-tooltip :content="s.error_message" placement="top" :show-after="200">
                  <span class="err-text">{{ s.error_message }}</span>
                </el-tooltip>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onUnmounted } from "vue"
import { ElMessage } from "element-plus"
import { listDocuments, getDocumentStatus, retryIngestion } from "@/api/admin"
import type { KbDocument, KbDocumentStatus } from "@/api/types"

const documents = ref<KbDocument[]>([])
const loading = ref(false)
const detailVisible = ref(false)
const detail = ref<KbDocumentStatus | null>(null)
const autoRefresh = ref(true)
const filterCategory = ref("")

// 常见分类下拉, 与 DocumentList 保持一致
const categoryOptions = ["faq", "信用卡", "贷款", "理财", "投诉处理", "通用知识"]

const ingestingCount = computed(() => documents.value.filter((d) => d.status === "ingesting").length)

let pollTimer: ReturnType<typeof setInterval> | null = null

async function load() {
  loading.value = true
  try {
    const res = await listDocuments({
      limit: 50,
      category: filterCategory.value || undefined,
    })
    documents.value = res.documents
  } catch {
    // handled by interceptor
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

async function onRetry(row: KbDocument) {
  try {
    await retryIngestion(row.doc_id)
    ElMessage.success(`已提交重试: ${row.title}`)
    load()
  } catch (e) {
    ElMessage.error(`重试失败: ${(e as Error).message ?? "未知错误"}`)
  }
}

async function copyError(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success("已复制错误信息")
  } catch {
    ElMessage.error("复制失败, 请手动选择")
  }
}

function onAutoToggle(v: boolean | string | number) {
  if (v) startPolling()
  else stopPolling()
}

function startPolling() {
  stopPolling()
  // 自动刷新: 3s 间隔, 切到后台 tab 暂停
  pollTimer = setInterval(() => {
    if (document.visibilityState === "visible") load()
  }, 3000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
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
function formatTime(s: string | null) {
  return s?.slice(0, 16).replace("T", " ") || "-"
}

load()
if (autoRefresh.value) startPolling()

onUnmounted(stopPolling)
</script>

<style scoped>
.monitor-page { max-width: 1200px; }
.page-header { display: flex; align-items: center; justify-content: space-between; }
.page-header h2 { margin: 0; font-size: 20px; }
.header-controls { display: flex; align-items: center; gap: 12px; }
.ingesting-badge { margin-right: 4px; }
.status-cell { display: flex; flex-direction: column; gap: 2px; }
.muted { color: #c0c4cc; }
.err-cell { display: flex; align-items: center; gap: 6px; }
.err-text {
  display: inline-block;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: #f56c6c;
}
</style>
