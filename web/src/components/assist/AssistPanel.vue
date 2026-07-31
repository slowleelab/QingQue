<template>
  <div class="assist-panel" data-testid="assist-panel">
  <div class="panel-header">
    <span class="panel-title">AI 辅助</span>
    <el-tag v-if="data" size="small" type="success" effect="dark">实时推送</el-tag>
  </div>

    <div v-if="!assistStore.activeSessionId" class="empty-panel">
      <p>选择会话后显示辅助信息</p>
    </div>

    <div v-else class="panel-content">
      <!-- 告警始终置顶 -->
      <div v-if="data?.alerts?.length" class="alerts-area">
        <AlertBanner v-for="(alert, i) in data.alerts" :key="i" :alert="alert" />
      </div>

      <!-- Tab 切换话术/知识/推荐 -->
      <el-tabs v-model="activeTab" class="assist-tabs">
        <el-tab-pane label="话术" name="scripts">
          <div v-if="data?.scripts?.length">
            <ScriptCard
              v-for="s in data.scripts"
              :key="s.script_id"
              :card="s"
              @adopt="onAdopt"
              @modify="onModify"
              @dismiss="onDismiss"
            />
          </div>
          <el-empty v-else description="暂无推荐话术" :image-size="48" />
        </el-tab-pane>

        <el-tab-pane label="知识" name="knowledge">
          <div v-if="data?.knowledge?.length">
            <KnowledgeSnippet v-for="k in data.knowledge" :key="k.chunk_id" :snippet="k" />
          </div>
          <el-empty v-else description="暂无知识片段" :image-size="48" />
        </el-tab-pane>

        <el-tab-pane label="推荐" name="recommendations">
          <div v-if="data?.recommendations?.length">
            <ProductRecommendation
              v-for="r in data.recommendations"
              :key="r.product_id"
              :recommendation="r"
            />
          </div>
          <el-empty v-else description="暂无产品推荐" :image-size="48" />
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from "vue"
import { ElMessage } from "element-plus"
import { useAssistStore } from "@/stores/assist"
import AlertBanner from "./AlertBanner.vue"
import ScriptCard from "./ScriptCard.vue"
import KnowledgeSnippet from "./KnowledgeSnippet.vue"
import ProductRecommendation from "./ProductRecommendation.vue"

const assistStore = useAssistStore()
const activeTab = ref("scripts")

const data = computed(() => assistStore.activePushData)

// 采纳 → 把推荐话术直接灌入坐席输入框
function onAdopt(scriptId: string) {
  const card = data.value?.scripts?.find((s) => s.script_id === scriptId)
  if (!card) return
  assistStore.insertDraftText(card.content)
  ElMessage.success("已采纳到输入框")
}

// 修改 → 先灌入文本, 坐席在输入框里继续编辑
function onModify(scriptId: string) {
  const card = data.value?.scripts?.find((s) => s.script_id === scriptId)
  if (!card) return
  assistStore.insertDraftText(card.content)
  ElMessage.info("已填充, 请在输入框调整后发送")
}

// 关闭 → 从本次推送中移除该脚本 (本会话内不再展示)
function onDismiss(scriptId: string) {
  if (data.value?.scripts) {
    data.value.scripts = data.value.scripts.filter((s) => s.script_id !== scriptId)
  }
}
</script>

<style scoped>
.assist-panel {
  width: 360px;
  display: flex;
  flex-direction: column;
  background: #fff;
  overflow: hidden;
}

.panel-header {
  padding: 12px 16px;
  border-bottom: 1px solid #ebeef5;
  background: #fafbfc;
}

.panel-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.empty-panel {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #c0c4cc;
}

.panel-content {
  flex: 1;
  overflow-y: auto;
  padding: 0 12px 12px;
}

.alerts-area {
  padding: 12px 0;
}

.assist-tabs :deep(.el-tabs__header) {
  margin-bottom: 8px;
}

.assist-tabs :deep(.el-tabs__item) {
  font-size: 13px;
}

.assist-tabs :deep(.el-empty) {
  padding: 24px 0;
}
</style>
