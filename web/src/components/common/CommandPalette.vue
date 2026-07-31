<template>
  <el-dialog
    v-model="visible"
    title="命令面板"
    width="480px"
    :show-close="true"
    :close-on-click-modal="true"
    data-testid="command-palette"
    @close="onClose"
  >
    <el-input
      v-model="query"
      placeholder="输入命令或操作…"
      :prefix-icon="Search"
      ref="inputRef"
      @keydown.enter.exact.prevent="runSelected"
      @keydown.down.exact.prevent="moveSel(1)"
      @keydown.up.exact.prevent="moveSel(-1)"
    />
    <ul class="cmd-list" v-if="filtered.length">
      <li
        v-for="(item, i) in filtered"
        :key="item.id"
        :class="{ active: i === selected }"
        @click="runItem(item)"
      >
        <span class="cmd-label">{{ item.label }}</span>
        <span class="cmd-hint">{{ item.hint }}</span>
      </li>
    </ul>
    <el-empty v-else description="无匹配命令" :image-size="60" />
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick } from "vue"
import { useRouter } from "vue-router"
import { Search } from "@element-plus/icons-vue"

interface CmdItem {
  id: string
  label: string
  hint?: string
  run: () => void | Promise<void>
}

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ "update:modelValue": [v: boolean] }>()

const router = useRouter()
const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit("update:modelValue", v),
})

const query = ref("")
const selected = ref(0)
const inputRef = ref()

// 占位命令集 — 后续接路由/主题切换/全局搜索等
// TODO: 全局搜索 (后续 sprint), 主题切换 (C3 useColorMode)
const items: CmdItem[] = [
  {
    id: "goto-workbench",
    label: "前往：坐席工作台",
    hint: "/agent",
    run: () => navigate("/agent"),
  },
  {
    id: "goto-customer",
    label: "前往：客户对话",
    hint: "/",
    run: () => navigate("/"),
  },
  {
    id: "goto-admin",
    label: "前往：管理后台",
    hint: "/admin",
    run: () => navigate("/admin"),
  },
  {
    id: "goto-login",
    label: "前往：登录",
    hint: "/login",
    run: () => navigate("/login"),
  },
]

function navigate(path: string) {
  router.push(path)
  onClose()
}

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return items
  return items.filter((i) => i.label.toLowerCase().includes(q))
})

watch(filtered, () => { selected.value = 0 })

function moveSel(delta: number) {
  if (!filtered.value.length) return
  selected.value = (selected.value + delta + filtered.value.length) % filtered.value.length
}

function runSelected() {
  if (filtered.value[selected.value]) runItem(filtered.value[selected.value])
}

function runItem(it: CmdItem) {
  it.run()
}

function onClose() {
  query.value = ""
  selected.value = 0
  visible.value = false
}

watch(visible, async (v) => {
  if (v) {
    await nextTick()
    inputRef.value?.focus?.()
  }
})
</script>

<style scoped>
.cmd-list {
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
  max-height: 320px;
  overflow-y: auto;
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 6px;
}
.cmd-list li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  cursor: pointer;
  font-size: 14px;
  border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5);
}
.cmd-list li:last-child { border-bottom: 0; }
.cmd-list li.active,
.cmd-list li:hover { background: var(--el-fill-color-light, #ecf5ff); }
.cmd-hint { color: var(--el-text-color-secondary, #909399); font-size: 12px; }
</style>
