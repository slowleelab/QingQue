<template>
  <div class="admin-layout">
    <aside class="sidebar">
      <div class="sidebar-header">
        <h3>SmartCS 管理</h3>
      </div>
      <el-menu
        :default-active="activeMenu"
        router
        background-color="#1d1e2c"
        text-color="#c9cdd4"
        active-text-color="#409eff"
      >
        <el-menu-item index="/admin/documents">
          <el-icon><Document /></el-icon>
          <span>文档管理</span>
        </el-menu-item>
        <el-menu-item index="/admin/faq">
          <el-icon><Collection /></el-icon>
          <span>FAQ 管理</span>
        </el-menu-item>
        <el-menu-item index="/admin/monitor">
          <el-icon><Monitor /></el-icon>
          <span>摄入监控</span>
        </el-menu-item>
      </el-menu>
      <div class="sidebar-footer">
        <el-button text size="small" @click="$router.push('/')">← 返回前台</el-button>
      </div>
    </aside>
    <main class="content">
      <router-view />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue"
import { useRoute } from "vue-router"
import { Document, Collection, Monitor } from "@element-plus/icons-vue"

const route = useRoute()
const activeMenu = computed(() => route.path)
</script>

<style scoped>
.admin-layout {
  display: flex;
  height: 100vh;
  background: #f0f2f5;
}
.sidebar {
  width: 220px;
  background: #1d1e2c;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}
.sidebar-header {
  padding: 20px 16px 12px;
  color: #fff;
  border-bottom: 1px solid #2d2e3c;
}
.sidebar-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}
.sidebar-footer {
  margin-top: auto;
  padding: 16px;
  border-top: 1px solid #2d2e3c;
}
.content {
  flex: 1;
  overflow: auto;
  padding: 24px;
}
</style>
