<template>
  <div class="monitor-page">
    <div class="page-header">
      <h2>摄入管道监控</h2>
      <div class="header-controls">
        <span v-if="monitor.isPolling.value" class="live-indicator" data-testid="live-indicator">
          <span class="dot" /> 实时
        </span>
        <span v-if="monitor.elapsedMs.value >= 0" class="elapsed-text">
          上次刷新 {{ monitor.elapsedMs.value }}ms
        </span>
        <el-badge
          v-if="ingestingCount > 0"
          :value="ingestingCount"
          class="ingesting-badge"
          type="warning"
        >
          <el-tag type="warning" size="small">摄入中</el-tag>
        </el-badge>
        <el-switch
          :model-value="monitor.isPolling.value"
          active-text="自动刷新"
          inline-prompt
          @update:model-value="(v: boolean) => v ? monitor.start() : monitor.stop()"
        />
        <el-select v-model="filterCategory" placeholder="分类" clearable size="small" style="width: 140px" @change="monitor.refresh">
          <el-option label="全部" value="" />
          <el-option v-for="c in categoryOptions" :key="c" :label="c" :value="c" />
        </el-select>
        <el-button :loading="monitor.loading.value" @click="monitor.refresh">刷新</el-button>
      </div>
    </div>

    <el-table :data="documents" v-loading="monitor.loading.value" stripe style="margin-top: 16px" data-testid="ingestion-table">
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

    <el-dialog v-model="detailVisible" title="摄入详情" width="680px" data-testid="ingestion-detail">
      <div v-if="detail">
        <p><strong>文档：</strong>{{ detail.title }}</p>
        <p>
          <strong>状态：</strong>
          <el-tag :type="statusType(detail.status)">{{ statusText(detail.status) }}</el-tag>
          <span v-if="totalDuration > 0" class="total-duration">总耗时 {{ totalDuration }}ms</span>
        </p>

        <!-- 时间线视图: 7 阶段顺序展示 -->
        <h4 class="timeline-title">处理时间线</h4>
        <el-timeline v-if="stages.length > 0" class="stage-timeline">
          <el-timeline-item
            v-for="(s, i) in stages"
            :key="i"
            :type="timelineType(s.status)"
            :timestamp="s.duration_ms + 'ms'"
            :hollow="s.status !== 'completed'"
            size="normal"
          >
            <div class="stage-line">
              <span class="stage-name">{{ stageLabel(s.stage) }}</span>
              <el-tag :type="rowStatusTag(s.status)" size="small">{{ s.status }}</el-tag>
            </div>
            <pre v-if="s.error_message" class="error-block" data-testid="stage-error">{{ s.error_message }}</pre>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="暂无阶段数据" :image-size="60" />

        <!-- 错误聚合块: 失败时一次复制全部错误 -->
        <div v-if="failedErrors.length > 0" class="error-aggregate">
          <div class="error-aggregate-header">
            <strong>失败信息（{{ failedErrors.length }} 处）</strong>
            <el-button link type="primary" size="small" @click="copyAllErrors">一键复制</el-button>
          </div>
          <pre class="error-block" data-testid="error-aggregate">{{ failedErrors.join("\n\n---\n\n") }}</pre>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from "vue"
import { ElMessage } from "element-plus"
import { listDocuments, getDocumentStatus, retryIngestion } from "@/api/admin"
import { useIngestionMonitor } from "@/composables/useIngestionMonitor"
import type { KbDocument, KbDocumentStatus } from "@/api/types"

const documents = ref<KbDocument[]>([])
const detailVisible = ref(false)
const detail = ref<KbDocumentStatus | null>(null)
const filterCategory = ref("")

const categoryOptions = ["faq", "信用卡", "贷款", "理财", "投诉处理", "通用知识"]

const ingestingCount = computed(() => documents.value.filter((d) => d.status === "ingesting").length)

async function load() {
  const res = await listDocuments({
    limit: 50,
    category: filterCategory.value || undefined,
  })
  documents.value = res.documents
}

const monitor = useIngestionMonitor({ loader: load, intervalMs: 3000, autoStart: true })

async function viewDetail(row: KbDocument) {
  try {
    detail.value = await getDocumentStatus(row.doc_id)
    detailVisible.value = true
  } catch {
    /* handled */
  }
}

async function onRetry(row: KbDocument) {
  try {
    await retryIngestion(row.doc_id)
    ElMessage.success(`已提交重试: ${row.title}`)
    monitor.refresh()
  } catch (e) {
    ElMessage.error(`重试失败: ${(e as Error).message ?? "未知错误"}`)
  }
}

async function copyAllErrors() {
  if (!detail.value) return
  const text = failedErrors.value.join("\n\n---\n\n")
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success("已复制全部错误信息")
  } catch {
    ElMessage.error("复制失败, 请手动选择")
  }
}

// ── 时间线派生 ──
const stages = computed(() => detail.value?.stages ?? [])
const totalDuration = computed(() => stages.value.reduce((s, x) => s + (x.duration_ms || 0), 0))
const failedErrors = computed(() => stages.value.map((s) => s.error_message).filter((e): e is string => Boolean(e)))

function stageLabel(stage: string) {
  const m: Record<string, string> = {
    upload: "上传", parse: "解析", chunk: "分块", embed: "向量化",
    index: "建索引", validate: "校验", finalize: "完成",
  }
  return m[stage] ?? stage
}
function timelineType(s: string): "primary" | "success" | "warning" | "danger" | "info" {
  if (s === "completed") return "success"
  if (s === "failed")    return "danger"
  if (s === "running")   return "warning"
  return "info"
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
</script>

<style scoped>
.monitor-page { max-width: 1200px; }
.page-header { display: flex; align-items: center; justify-content: space-between; }
.page-header h2 { margin: 0; font-size: var(--fs-2xl); }
.header-controls { display: flex; align-items: center; gap: var(--space-3); }
.ingesting-badge { margin-right: 4px; }
.live-indicator {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--fs-sm);
  color: var(--color-success);
}
.live-indicator .dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--color-success);
  box-shadow: 0 0 0 0 var(--color-success);
  animation: pulse 1.6s infinite;
}
@keyframes pulse {
  0%   { box-shadow: 0 0 0 0   rgba(103, 194, 58, 0.5); }
  70%  { box-shadow: 0 0 0 6px rgba(103, 194, 58, 0);   }
  100% { box-shadow: 0 0 0 0   rgba(103, 194, 58, 0);   }
}
.elapsed-text {
  font-size: var(--fs-xs);
  color: var(--color-text-secondary);
}
.status-cell { display: flex; flex-direction: column; gap: 2px; }
.timeline-title {
  margin: var(--space-4) 0 var(--space-2);
  font-size: var(--fs-md);
  color: var(--color-text-primary);
}
.stage-timeline {
  padding-left: var(--space-2);
}
.stage-line {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.stage-name {
  font-weight: 500;
  color: var(--color-text-primary);
}
.error-block {
  margin-top: var(--space-2);
  padding: var(--space-2) var(--space-3);
  background: var(--color-bg-page);
  border-left: 3px solid var(--color-danger);
  border-radius: var(--radius-sm);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: var(--fs-sm);
  color: var(--color-danger);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 240px;
  overflow-y: auto;
}
.error-aggregate {
  margin-top: var(--space-4);
  padding: var(--space-3);
  background: var(--color-bg-page);
  border-radius: var(--radius-md);
}
.error-aggregate-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-2);
}
.total-duration {
  margin-left: var(--space-3);
  font-size: var(--fs-sm);
  color: var(--color-text-secondary);
}
</style>
