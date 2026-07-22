<template>
  <div class="faq-page">
    <div class="page-header">
      <h2>
        FAQ 管理
        <el-badge v-if="pendingCount > 0" :value="pendingCount" class="item" style="margin-left: 8px" />
      </h2>
      <div class="header-actions">
        <el-button
          v-if="pendingCount > 0"
          type="warning"
          plain
          size="small"
          @click="filterStatus = 'IN_REVIEW'; load()"
        >
          待审核 ({{ pendingCount }})
        </el-button>
        <el-select v-model="filterStatus" placeholder="审批状态" clearable style="width: 140px" @change="load">
          <el-option label="草稿" value="DRAFT" />
          <el-option label="审核中" value="IN_REVIEW" />
          <el-option label="已通过" value="APPROVED" />
          <el-option label="已发布" value="PUBLISHED" />
          <el-option label="已驳回" value="REJECTED" />
        </el-select>
        <el-input
          v-model="filterCategory"
          placeholder="分类"
          clearable
          style="width: 140px"
          @change="load"
        />
        <el-button type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon>
          新建 FAQ
        </el-button>
      </div>
    </div>

    <!-- FAQ 表格 -->
    <el-table :data="faqs" v-loading="loading" stripe style="margin-top: 16px" @row-click="openDetail">
      <el-table-column prop="question" label="问题" min-width="240" show-overflow-tooltip />
      <el-table-column prop="category" label="分类" width="100" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="approvalTagType(row.approval_status)" size="small">
            {{ approvalText(row.approval_status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="version" label="版本" width="60" align="center" />
      <el-table-column prop="created_at" label="创建时间" width="160">
        <template #default="{ row }">
          {{ row.created_at?.slice(0, 16).replace("T", " ") || "-" }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <template v-if="row.approval_status === 'DRAFT'">
            <el-button link type="primary" size="small" @click.stop="doSubmit(row.id)">提交</el-button>
          </template>
          <template v-else-if="row.approval_status === 'IN_REVIEW'">
            <el-button link type="success" size="small" @click.stop="doApprove(row.id)">通过</el-button>
            <el-button link type="danger" size="small" @click.stop="doReject(row.id)">驳回</el-button>
          </template>
          <template v-else-if="row.approval_status === 'APPROVED'">
            <el-button link type="success" size="small" @click.stop="doPublish(row.id)">发布</el-button>
          </template>
          <template v-else-if="row.approval_status === 'PUBLISHED'">
            <el-button link type="warning" size="small" @click.stop="doArchive(row.id)">归档</el-button>
          </template>
          <el-button link size="small" @click.stop="openEdit(row.id)">编辑</el-button>
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

    <!-- 详情 / 编辑抽屉 -->
    <el-drawer
      v-model="drawerVisible"
      :title="editingId ? '编辑 FAQ' : 'FAQ 详情'"
      size="560px"
      @close="resetDetail"
    >
      <el-form v-if="detail" :model="editForm" label-width="100px" :disabled="!editing">
        <el-form-item label="主问题" required>
          <el-input v-model="editForm.question" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="答案" required>
          <el-input v-model="editForm.answer" type="textarea" :rows="4" />
        </el-form-item>
        <el-form-item label="变体问法">
          <div v-for="(v, i) in editForm.variant_questions" :key="i" class="variant-row">
            <el-input v-model="editForm.variant_questions[i]" size="small" />
            <el-button link type="danger" size="small" @click="editForm.variant_questions.splice(i, 1)">删除</el-button>
          </div>
          <el-button size="small" @click="editForm.variant_questions.push('')">+ 添加变体</el-button>
        </el-form-item>
        <el-form-item label="分类">
          <el-select v-model="editForm.category" style="width: 100%">
            <el-option label="年费政策" value="annual_fee" />
            <el-option label="账单规则" value="billing" />
            <el-option label="额度相关" value="credit_limit" />
            <el-option label="分期业务" value="installment" />
            <el-option label="积分权益" value="rewards" />
            <el-option label="挂失补卡" value="card_loss" />
            <el-option label="合规政策" value="compliance" />
            <el-option label="常见问题" value="faq" />
          </el-select>
        </el-form-item>
        <el-form-item label="关键词">
          <el-select v-model="editForm.keywords" multiple filterable allow-create placeholder="输入关键词后回车" style="width: 100%" />
        </el-form-item>
        <el-form-item label="卡种">
          <el-select v-model="editForm.card_types" multiple style="width: 100%">
            <el-option label="普卡" value="standard" />
            <el-option label="金卡" value="gold" />
            <el-option label="白金卡" value="platinum" />
            <el-option label="钻石卡" value="diamond" />
          </el-select>
        </el-form-item>
        <el-form-item label="审批状态">
          <el-tag :type="approvalTagType(detail.approval_status)">
            {{ approvalText(detail.approval_status) }}
          </el-tag>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button v-if="!editing" @click="editing = true">编辑</el-button>
        <el-button v-if="editing" @click="editing = false; resetEditForm()">取消</el-button>
        <el-button v-if="editing" type="primary" :loading="saving" @click="doSave">
          {{ saving ? "保存中..." : "保存" }}
        </el-button>
      </template>
    </el-drawer>

    <!-- 新建 FAQ 对话框 -->
    <el-dialog v-model="createVisible" title="新建 FAQ" width="520px">
      <el-form :model="createForm" label-width="100px">
        <el-form-item label="主问题" required>
          <el-input v-model="createForm.question" />
        </el-form-item>
        <el-form-item label="答案" required>
          <el-input v-model="createForm.answer" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="分类" required>
          <el-select v-model="createForm.category" style="width: 100%">
            <el-option label="年费政策" value="annual_fee" />
            <el-option label="账单规则" value="billing" />
            <el-option label="额度相关" value="credit_limit" />
            <el-option label="分期业务" value="installment" />
            <el-option label="积分权益" value="rewards" />
            <el-option label="挂失补卡" value="card_loss" />
            <el-option label="合规政策" value="compliance" />
            <el-option label="常见问题" value="faq" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="doCreate">
          {{ creating ? "创建中..." : "创建" }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from "vue"
import { ElMessage, ElMessageBox } from "element-plus"
import { Plus } from "@element-plus/icons-vue"
import {
  listFaqs, getFaq, createFaq, updateFaq, deleteFaq,
  submitFaq, approveFaq, rejectFaq, publishFaq, archiveFaq,
} from "@/api/admin"
import type { FaqItem, FaqDetail } from "@/api/types"

// ── 表格数据 ──
const faqs = ref<FaqItem[]>([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const filterStatus = ref("")
const filterCategory = ref("")
const pendingCount = ref(0)

async function load() {
  loading.value = true
  try {
    const res = await listFaqs({
      approval_status: filterStatus.value || undefined,
      category: filterCategory.value || undefined,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    faqs.value = res.faqs
    total.value = res.total
  } catch {
    // handled
  } finally {
    loading.value = false
  }
}

// ── 详情 / 编辑 ──
const drawerVisible = ref(false)
const editing = ref(false)
const editingId = ref("")
const detail = ref<FaqDetail | null>(null)
const saving = ref(false)
const editForm = reactive({
  question: "", answer: "", variant_questions: [] as string[],
  category: "", keywords: [] as string[], card_types: [] as string[],
})

async function openDetail(row: FaqItem) {
  editing.value = false
  editingId.value = row.id
  detail.value = await getFaq(row.id)
  resetEditForm()
  drawerVisible.value = true
}

async function openEdit(faqId: string) {
  editingId.value = faqId
  detail.value = await getFaq(faqId)
  resetEditForm()
  editing.value = true
  drawerVisible.value = true
}

function resetEditForm() {
  if (!detail.value) return
  editForm.question = detail.value.question
  editForm.answer = detail.value.answer
  editForm.variant_questions = [...(detail.value.variant_questions || [])]
  editForm.category = detail.value.category
  editForm.keywords = [...(detail.value.keywords || [])]
  editForm.card_types = [...(detail.value.card_types || [])]
}

function resetDetail() {
  detail.value = null
  editing.value = false
  editingId.value = ""
}

async function doSave() {
  saving.value = true
  try {
    await updateFaq(editingId.value, {
      question: editForm.question,
      answer: editForm.answer,
      variant_questions: editForm.variant_questions.filter(Boolean),
      category: editForm.category,
      keywords: editForm.keywords,
      card_types: editForm.card_types,
    })
    ElMessage.success("已保存")
    editing.value = false
    load()
  } catch {
    // handled
  } finally {
    saving.value = false
  }
}

// ── 新建 ──
const createVisible = ref(false)
const creating = ref(false)
const createForm = reactive({ question: "", answer: "", category: "faq" })

function openCreate() {
  createForm.question = ""
  createForm.answer = ""
  createForm.category = "faq"
  createVisible.value = true
}

async function doCreate() {
  creating.value = true
  try {
    await createFaq({
      question: createForm.question,
      answer: createForm.answer,
      category: createForm.category,
    })
    ElMessage.success("创建成功（状态：草稿）")
    createVisible.value = false
    load()
  } catch {
    // handled
  } finally {
    creating.value = false
  }
}

// ── 审批操作 ──
async function doSubmit(id: string)  { await submitFaq(id); ElMessage.success("已提交审核"); load() }
async function doApprove(id: string) { await approveFaq(id); ElMessage.success("已通过"); load() }
async function doReject(id: string)  { await rejectFaq(id); ElMessage.success("已驳回"); load() }
async function doPublish(id: string) { await publishFaq(id); ElMessage.success("已发布"); load() }
async function doArchive(id: string) { await archiveFaq(id); ElMessage.success("已归档"); load() }

// ── 辅助 ──
function approvalTagType(s: string) {
  const m: Record<string, string> = {
    DRAFT: "info", IN_REVIEW: "warning", APPROVED: "", PUBLISHED: "success",
    REJECTED: "danger", SUPERSEDED: "info", ARCHIVED: "info",
  }
  return m[s] ?? "info"
}
function approvalText(s: string) {
  const m: Record<string, string> = {
    DRAFT: "草稿", IN_REVIEW: "审核中", APPROVED: "已通过", PUBLISHED: "已发布",
    REJECTED: "已驳回", SUPERSEDED: "已取代", ARCHIVED: "已归档",
  }
  return m[s] ?? s
}

load()
loadPendingCount()

async function loadPendingCount() {
  try {
    const res = await listFaqs({ approval_status: "IN_REVIEW", limit: 1 })
    pendingCount.value = res.total
  } catch { /* ignore */ }
}
</script>

<style scoped>
.faq-page {
  max-width: 1200px;
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.page-header h2 {
  margin: 0;
  font-size: 20px;
}
.header-actions {
  display: flex;
  gap: 12px;
}
.pagination-wrap {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
.variant-row {
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
}
</style>
