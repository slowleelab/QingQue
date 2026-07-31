<template>
  <el-drawer
    :model-value="visible"
    :title="detail && editing ? '编辑 FAQ' : 'FAQ 详情'"
    size="560px"
    @update:model-value="(v: boolean) => emit('update:visible', v)"
    @close="emit('close')"
  >
    <el-form v-if="detail" :model="editForm" label-width="100px" :disabled="!editing">
      <el-form-item label="主问题" required>
        <el-input v-model="editForm.question" type="textarea" :rows="2" />
      </el-form-item>
      <el-form-item label="答案" required>
        <el-input v-model="editForm.answer" type="textarea" :rows="4" />
      </el-form-item>
      <el-form-item label="变体问法">
        <div v-for="(_, i) in editForm.variant_questions" :key="i" class="variant-row">
          <el-input v-model="editForm.variant_questions[i]" size="small" />
          <el-button link type="danger" size="small" @click="editForm.variant_questions.splice(i, 1)">删除</el-button>
        </div>
        <el-button size="small" @click="editForm.variant_questions.push('')">+ 添加变体</el-button>
      </el-form-item>
      <el-form-item label="分类">
        <el-select v-model="editForm.category" style="width: 100%">
          <el-option v-for="c in CATEGORIES" :key="c.value" :label="c.label" :value="c.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="关键词">
        <el-select v-model="editForm.keywords" multiple filterable allow-create placeholder="输入关键词后回车" style="width: 100%" />
      </el-form-item>
      <el-form-item label="卡种">
        <el-select v-model="editForm.card_types" multiple style="width: 100%">
          <el-option v-for="t in CARD_TYPES" :key="t.value" :label="t.label" :value="t.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="审批状态">
        <el-tag :type="tagType(detail.approval_status)">{{ text(detail.approval_status) }}</el-tag>
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button v-if="!editing" @click="emit('start-edit')">编辑</el-button>
      <template v-else>
        <el-button @click="emit('cancel-edit')">取消</el-button>
        <el-button type="primary" :loading="saving" @click="emit('save')">
          {{ saving ? "保存中..." : "保存" }}
        </el-button>
      </template>
    </template>
  </el-drawer>
</template>

<script setup lang="ts">
import { reactive, watch } from "vue"
import type { FaqDetail } from "@/api/types"
import { useFaqStatus } from "@/composables/useFaqStatus"

const CATEGORIES = [
  { label: "年费政策", value: "annual_fee" },
  { label: "账单规则", value: "billing" },
  { label: "额度相关", value: "credit_limit" },
  { label: "分期业务", value: "installment" },
  { label: "积分权益", value: "rewards" },
  { label: "挂失补卡", value: "card_loss" },
  { label: "合规政策", value: "compliance" },
  { label: "常见问题", value: "faq" },
]
const CARD_TYPES = [
  { label: "普卡",   value: "standard" },
  { label: "金卡",   value: "gold" },
  { label: "白金卡", value: "platinum" },
  { label: "钻石卡", value: "diamond" },
]

const props = defineProps<{
  visible: boolean
  detail: FaqDetail | null
  editing: boolean
  saving: boolean
}>()

const emit = defineEmits<{
  (e: "update:visible", v: boolean): void
  (e: "close"): void
  (e: "start-edit"): void
  (e: "cancel-edit"): void
  (e: "save"): void
}>()

const { tagType, text } = useFaqStatus()

const editForm = reactive({
  question: "", answer: "", variant_questions: [] as string[],
  category: "", keywords: [] as string[], card_types: [] as string[],
})

watch(() => props.detail, (d) => {
  if (!d) return
  editForm.question = d.question
  editForm.answer = d.answer
  editForm.variant_questions = [...(d.variant_questions || [])]
  editForm.category = d.category
  editForm.keywords = [...(d.keywords || [])]
  editForm.card_types = [...(d.card_types || [])]
}, { immediate: true })

defineExpose({ editForm })
</script>

<style scoped>
.variant-row {
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
}
</style>
