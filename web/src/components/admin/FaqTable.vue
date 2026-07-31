<template>
  <el-table :data="faqs" v-loading="loading" stripe style="margin-top: 16px" @row-click="onRowClick">
    <el-table-column prop="question" label="问题" min-width="240" show-overflow-tooltip />
    <el-table-column prop="category" label="分类" width="100" />
    <el-table-column label="状态" width="100">
      <template #default="{ row }">
        <el-tag :type="tagType(row.approval_status)" size="small">
          {{ text(row.approval_status) }}
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
          <el-button link type="primary" size="small" @click.stop="emitAction('submit', row)">提交</el-button>
        </template>
        <template v-else-if="row.approval_status === 'IN_REVIEW'">
          <el-button link type="success" size="small" @click.stop="emitAction('approve', row)">通过</el-button>
          <el-button link type="danger"  size="small" @click.stop="emitAction('reject',  row)">驳回</el-button>
        </template>
        <template v-else-if="row.approval_status === 'APPROVED'">
          <el-button link type="success" size="small" @click.stop="emitAction('publish', row)">发布</el-button>
        </template>
        <template v-else-if="row.approval_status === 'PUBLISHED'">
          <el-button link type="warning" size="small" @click.stop="emitAction('archive', row)">归档</el-button>
        </template>
        <el-button link size="small" @click.stop="emitAction('edit', row)">编辑</el-button>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
import type { FaqItem } from "@/api/types"
import { useFaqStatus } from "@/composables/useFaqStatus"

const props = defineProps<{
  faqs: FaqItem[]
  loading: boolean
}>()

const emit = defineEmits<{
  (e: "row-click", row: FaqItem): void
  (e: "action",   action: "submit" | "approve" | "reject" | "publish" | "archive" | "edit", row: FaqItem): void
}>()

const { tagType, text } = useFaqStatus()

function onRowClick(row: FaqItem) { emit("row-click", row) }
function emitAction(action: Parameters<typeof emit>[1], row: FaqItem) { emit("action", action, row) }
</script>
