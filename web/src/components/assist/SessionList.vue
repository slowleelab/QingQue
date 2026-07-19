<template>
  <div class="session-list" data-testid="session-list">
    <div class="list-header">
      <span class="title">会话列表</span>
      <el-badge :value="activeCount" :max="99" type="primary" />
    </div>
    <div class="list-body">
      <div
        v-for="session in assistStore.sessions"
        :key="session.sessionId"
        class="session-item" data-testid="session-item"
        :class="{ active: session.sessionId === assistStore.activeSessionId }"
        @click="assistStore.selectSession(session.sessionId)"
      >
        <div class="session-name">{{ session.customerName || session.sessionId }}</div>
        <div class="session-meta">
          <el-tag :type="phaseTagType(session.phase)" size="small">
            {{ phaseLabel(session.phase) }}
          </el-tag>
          <span class="session-time">{{ formatTime(session.lastActiveAt) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue"
import { useAssistStore } from "@/stores/assist"
import type { SessionPhase } from "@/api/types"

const assistStore = useAssistStore()

const activeCount = computed(
  () => assistStore.sessions.filter((s) => s.phase !== "ended").length,
)

const phaseTagMap: Record<SessionPhase, { type: "" | "warning" | "success" | "danger"; label: string }> = {
  bot: { type: "", label: "机器人" },
  agent: { type: "success", label: "坐席辅助" },
  ended: { type: "danger", label: "已结束" },
}

function phaseTagType(phase: SessionPhase) {
  return phaseTagMap[phase].type
}

function phaseLabel(phase: SessionPhase) {
  return phaseTagMap[phase].label
}

function formatTime(date: Date): string {
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
}
</script>

<style scoped>
.session-list {
  width: 240px;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-right: 1px solid #ebeef5;
}

.list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px;
  border-bottom: 1px solid #ebeef5;
}

.list-header .title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.list-body {
  flex: 1;
  overflow-y: auto;
}

.session-item {
  padding: 12px 16px;
  cursor: pointer;
  border-bottom: 1px solid #f2f3f5;
  transition: background 0.2s;
}

.session-item:hover {
  background: #f5f7fa;
}

.session-item.active {
  background: #ecf5ff;
}

.session-name {
  font-size: 14px;
  font-weight: 500;
  color: #303133;
  margin-bottom: 6px;
}

.session-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.session-time {
  font-size: 11px;
  color: #c0c4cc;
}
</style>
