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
          @click="onJumpToPending"
        >
          待审核 ({{ pendingCount }})
        </el-button>
        <el-select v-model="filterStatus" placeholder="审批状态" clearable style="width: 140px" @change="onFilterChange">
          <el-option label="草稿"   value="DRAFT" />
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
          @change="onFilterChange"
        />
        <el-button type="primary" @click="createVisible = true">
          <el-icon><Plus /></el-icon>
          新建 FAQ
        </el-button>
      </div>
    </div>

    <FaqTable
      :faqs="faqs"
      :loading="loading"
      @row-click="openDetail"
      @action="onAction"
    />

    <div class="pagination-wrap">
      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        @current-change="load"
      />
    </div>

    <FaqEditDrawer
      ref="drawerRef"
      v-model:visible="drawerVisible"
      :detail="detail"
      :editing="editing"
      :saving="saving"
      @close="resetDetail"
      @start-edit="editing = true"
      @cancel-edit="editing = false"
      @save="doSave"
    />

    <FaqCreateDialog
      v-model:visible="createVisible"
      :creating="creating"
      @create="doCreate"
    />
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue"
import { ElMessage } from "element-plus"
import { Plus } from "@element-plus/icons-vue"
import { getFaq, updateFaq, createFaq as doCreateFaq } from "@/api/admin"
import { useFaqAdmin } from "@/composables/useFaqAdmin"
import type { FaqItem, FaqDetail } from "@/api/types"
import FaqTable from "@/components/admin/FaqTable.vue"
import FaqEditDrawer from "@/components/admin/FaqEditDrawer.vue"
import FaqCreateDialog from "@/components/admin/FaqCreateDialog.vue"

const {
  faqs, total, loading, page, pageSize, filterStatus, filterCategory, pendingCount,
  load, loadPendingCount, reset,
  submit, approve, reject, publish, archive,
} = useFaqAdmin()

// ── 详情 / 编辑 ──
const drawerVisible = ref(false)
const editing = ref(false)
const detail = ref<FaqDetail | null>(null)
const saving = ref(false)
const drawerRef = ref<InstanceType<typeof FaqEditDrawer> | null>(null)

async function openDetail(row: FaqItem) {
  editing.value = false
  detail.value = await getFaq(row.id)
  drawerVisible.value = true
}

function resetDetail() {
  detail.value = null
  editing.value = false
}

async function doSave() {
  if (!detail.value || !drawerRef.value) return
  const form = drawerRef.value.editForm
  saving.value = true
  try {
    await updateFaq(detail.value.id, {
      question: form.question,
      answer: form.answer,
      variant_questions: form.variant_questions.filter(Boolean),
      category: form.category,
      keywords: form.keywords,
      card_types: form.card_types,
    })
    ElMessage.success("已保存")
    editing.value = false
    load()
  } finally {
    saving.value = false
  }
}

// ── 新建 ──
const createVisible = ref(false)
const creating = ref(false)

async function doCreate(payload: { question: string; answer: string; category: string }) {
  creating.value = true
  try {
    await doCreateFaq(payload)
    ElMessage.success("创建成功（状态：草稿）")
    createVisible.value = false
    load()
  } finally {
    creating.value = false
  }
}

// ── 表格 action 路由 ──
type FaqAction = "submit" | "approve" | "reject" | "publish" | "archive" | "edit"
async function onAction(action: FaqAction, row: FaqItem) {
  switch (action) {
    case "submit":  await submit(row.id);  break
    case "approve": await approve(row.id); break
    case "reject":  await reject(row.id);  break
    case "publish": await publish(row.id); break
    case "archive": await archive(row.id); break
    case "edit":
      detail.value = await getFaq(row.id)
      editing.value = true
      drawerVisible.value = true
      break
  }
}

function onFilterChange() { reset(); load() }
function onJumpToPending() { filterStatus.value = "IN_REVIEW"; reset(); load() }

onMounted(() => {
  load()
  loadPendingCount()
})
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
</style>
