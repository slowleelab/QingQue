import { ref } from "vue"
import { ElMessage } from "element-plus"
import {
  listFaqs,
  submitFaq, approveFaq, rejectFaq, publishFaq, archiveFaq,
} from "@/api/admin"
import type { FaqItem } from "@/api/types"

/** FaqManager 视图层共享的"列表加载 + 审批流"逻辑
 *
 *  - 不耦合 UI 组件, 纯数据/副作用
 *  - 列表 / 分页 / 过滤 / pending count / 审批 transitions 全集中此处
 *  - 组件只需调用 action, 不用关心 ElMessage 内部细节
 */
export function useFaqAdmin() {
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
      /* 错误已被 axios 拦截器 toast */
    } finally {
      loading.value = false
    }
  }

  async function loadPendingCount() {
    try {
      const res = await listFaqs({ approval_status: "IN_REVIEW", limit: 1 })
      pendingCount.value = res.total
    } catch { /* ignore */ }
  }

  function reset() {
    page.value = 1
  }

  // ── 审批 actions ──
  async function submit(id: string)  { await submitFaq(id);  ElMessage.success("已提交审核"); await load() }
  async function approve(id: string) { await approveFaq(id); ElMessage.success("已通过");     await load() }
  async function reject(id: string)  { await rejectFaq(id);  ElMessage.success("已驳回");     await load() }
  async function publish(id: string) { await publishFaq(id); ElMessage.success("已发布");     await load() }
  async function archive(id: string) { await archiveFaq(id); ElMessage.success("已归档");     await load() }

  return {
    faqs, total, loading, page, pageSize, filterStatus, filterCategory, pendingCount,
    load, loadPendingCount, reset,
    submit, approve, reject, publish, archive,
  }
}
