<template>
  <el-dialog :model-value="visible" title="新建 FAQ" width="520px" @update:model-value="(v: boolean) => emit('update:visible', v)">
    <el-form :model="form" label-width="100px">
      <el-form-item label="主问题" required>
        <el-input v-model="form.question" />
      </el-form-item>
      <el-form-item label="答案" required>
        <el-input v-model="form.answer" type="textarea" :rows="3" />
      </el-form-item>
      <el-form-item label="分类" required>
        <el-select v-model="form.category" style="width: 100%">
          <el-option v-for="c in CATEGORIES" :key="c.value" :label="c.label" :value="c.value" />
        </el-select>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="emit('update:visible', false)">取消</el-button>
      <el-button type="primary" :loading="creating" :disabled="!form.question" @click="onCreate">
        {{ creating ? "创建中..." : "创建" }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { reactive, watch } from "vue"

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

const props = defineProps<{
  visible: boolean
  creating: boolean
}>()

const emit = defineEmits<{
  (e: "update:visible", v: boolean): void
  (e: "create", payload: { question: string; answer: string; category: string }): void
}>()

const form = reactive({ question: "", answer: "", category: "faq" })

watch(() => props.visible, (v) => {
  if (v) {
    form.question = ""
    form.answer = ""
    form.category = "faq"
  }
})

function onCreate() {
  emit("create", { question: form.question, answer: form.answer, category: form.category })
}
</script>
