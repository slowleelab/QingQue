/** 全局快捷键 composable
 *
 * 用法: 在 App.vue setup() 中调用一次 registerGlobalShortcuts()
 * 后续版本可扩展更多 shortcut, 当前最小集 (1 commit scope):
 * - ⌘K / Ctrl+K: 打开命令面板
 * - Esc: 关闭顶层 dialog/drawer
 *
 * 显式留 TODO 接路由: 命令面板列出"切换主题 / 跳转坐席台"等占位动作.
 */

import { onMounted, onUnmounted } from "vue"

type Handler = (e: KeyboardEvent) => void

interface ShortcutDef {
  key: string             // 小写比较
  cmdOrCtrl?: boolean     // 默认 false; true 时允许 ⌘ 或 Ctrl
  handler: Handler
}

const registry = new Set<ShortcutDef>()

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return (
    tag === "INPUT" || tag === "TEXTAREA" || target.isContentEditable
  )
}

function onKeydown(e: KeyboardEvent) {
  // 忽略修饰键单独按下 (避免触发 ⌘K 之后再按 Esc 时被吞)
  if (["Meta", "Control", "Alt", "Shift"].includes(e.key)) return

  const cmd = e.metaKey || e.ctrlKey
  const k = e.key.toLowerCase()

  for (const def of registry) {
    if (def.key !== k) continue
    if (def.cmdOrCtrl && !cmd) continue
    if (!def.cmdOrCtrl && cmd) continue

    // Esc 总是允许在任何 input 内 (用来关闭面板, 不应该劫持)
    if (k === "escape") {
      def.handler(e)
      return
    }

    // 其他快捷键若焦点在可编辑元素内, 不劫持 (避免坐席打字时被吞)
    if (isEditableTarget(e.target) && k !== "escape") continue

    e.preventDefault()
    def.handler(e)
    return
  }
}

export function useShortcuts() {
  onMounted(() => window.addEventListener("keydown", onKeydown))
  onUnmounted(() => window.removeEventListener("keydown", onKeydown))
}

export function registerShortcut(def: ShortcutDef) {
  registry.add(def)
  return () => registry.delete(def)
}
