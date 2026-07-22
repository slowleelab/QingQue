<template>
  <div class="documents-page">
    <div class="page-header">
      <h2>文档管理</h2>
      <div class="header-actions">
        <el-input
          v-model="searchCategory"
          placeholder="按分类筛选"
          clearable
          style="width: 200px"
          @change="load"
        />
        <el-upload
          :auto-upload="false"
          :limit="1"
          accept=".md,.txt,.pdf,.doc,.docx,.html"
          :on-change="handleFileSelect"
          :show-file-list="false"
        >
          <el-button type="primary">
            <el-icon><Upload /></el-icon>
            上传文档
          </el-button>
        </el-upload>
      </div>
    </div>

    <!-- 上传对话框 -->
    <el-dialog v-model="uploadVisible" title="上传知识文档" width="480px">
      <el-form :model="uploadForm" label-width="80px">
        <el-form-item label="文件">
          <span>{{ uploadForm.file?.name }}</span>
        </el-form-item>
        <el-form-item label="分类" required>
          <el-select v-model="uploadForm.category" placeholder="选择文档分类" style="width: 100%">
            <el-option label="年费政策" value="annual_fee" />
            <el-option label="账单规则" value="billing" />
            <el-option label="额度相关" value="credit_limit" />
            <el-option label="分期业务" value="installment" />
            <el-option label="积分权益" value="rewards" />
            <el-option label="挂失补卡" value="card_loss" />
            <el-option label="合规政策" value="compliance" />
            <el-option label="常见问题" value="faq" />
            <el-option label="其他" value="general" />
          </el-select>
        </el-form-item>
        <el-form-item label="文档类型" required>
          <el-select v-model="uploadForm.doc_type" placeholder="选择文档类型" style="width: 100%">
            <el-option label="产品手册" value="product_manual" />
            <el-option label="政策规则" value="policy" />
            <el-option label="FAQ" value="faq" />
            <el-option label="营销素材" value="marketing" />
            <el-option label="培训资料" value="training" />
            <el-option label="其他" value="other" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="doUpload">
          {{ uploading ? "上传中..." : "确定上传" }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 文档表格 -->
    <el-table :data="documents" v-loading="loading" stripe style="margin-top: 16px">
      <el-table-column prop="title" label="文档标题" min-width="200" />
      <el-table-column prop="category" label="分类" width="120" />
      <el-table-column prop="doc_type" label="类型" width="100" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">
            {{ statusText(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="chunk_count" label="分块数" width="80" align="center" />
      <el-table-column prop="created_at" label="创建时间" width="180">
        <template #default="{ row }">
          {{ row.created_at?.slice(0, 16).replace("T", " ") || "-" }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="viewStatus(row)">状态</el-button>
          <el-popconfirm title="确定删除此文档？" @confirm="handleDelete(row.doc_id)">
            <template #reference>
              <el-button link type="danger" size="small">删除</el-button>
            </template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <div class="pagination-wrap">
      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        @current-change="load"
      />
    </div>

    <!-- 摄入状态弹窗 -->
    <el-dialog v-model="statusVisible" title="文档摄入状态" width="500px">
      <div v-if="docStatus">
        <p><strong>状态：</strong>
          <el-tag :type="statusType(docStatus.status)">{{ statusText(docStatus.status) }}</el-tag>
        </p>
        <el-table :data="docStatus.stages || []" size="small" style="margin-top: 12px">
          <el-table-column prop="stage" label="阶段" />
          <el-table-column prop="status" label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="row.status === 'completed' ? 'success' : 'info'" size="small">
                {{ row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="duration_ms" label="耗时(ms)" width="100" align="right" />
        </el-table>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from "vue"
import { ElMessage } from "element-plus"
import { Upload } from "@element-plus/icons-vue"
import { listDocuments, uploadDocument, deleteDocument, getDocumentStatus } from "@/api/admin"
import type { KbDocument, KbDocumentStatus } from "@/api/types"

const documents = ref<KbDocument[]>([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const searchCategory = ref("")

const uploadVisible = ref(false)
const uploading = ref(false)
const uploadForm = reactive({ file: null as File | null, category: "general", doc_type: "other" })

function handleFileSelect(file: { raw: File }) {
  uploadForm.file = file.raw
  uploadVisible.value = true
}

async function doUpload() {
  if (!uploadForm.file) return
  uploading.value = true
  try {
    await uploadDocument(uploadForm.file, uploadForm.category, uploadForm.doc_type)
    ElMessage.success("上传成功，等待摄入处理")
    uploadVisible.value = false
    uploadForm.file = null
    load()
  } catch {
    // handled by interceptor
  } finally {
    uploading.value = false
  }
}

async function load() {
  loading.value = true
  try {
    const res = await listDocuments({
      category: searchCategory.value || undefined,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    documents.value = res.documents
    total.value = res.total
  } catch {
    // handled
  } finally {
    loading.value = false
  }
}

async function handleDelete(docId: string) {
  try {
    await deleteDocument(docId)
    ElMessage.success("已删除")
    load()
  } catch {
    // handled
  }
}

const statusVisible = ref(false)
const docStatus = ref<KbDocumentStatus | null>(null)

async function viewStatus(row: KbDocument) {
  try {
    docStatus.value = await getDocumentStatus(row.doc_id)
    statusVisible.value = true
  } catch {
    // handled
  }
}

function statusType(s: string) {
  const m: Record<string, string> = {
    ingested: "success", ingesting: "warning", failed: "danger",
    pending: "info", deleted: "info",
  }
  return m[s] ?? "info"
}
function statusText(s: string) {
  const m: Record<string, string> = {
    ingested: "已就绪", ingesting: "摄入中", failed: "失败",
    pending: "待处理", deleted: "已删除",
  }
  return m[s] ?? s
}

load()
</script>

<style scoped>
.documents-page { max-width: 1200px; }
.page-header { display: flex; align-items: center; justify-content: space-between; }
.page-header h2 { margin: 0; font-size: 20px; }
.header-actions { display: flex; gap: 12px; }
.pagination-wrap { margin-top: 16px; display: flex; justify-content: flex-end; }
</style>
