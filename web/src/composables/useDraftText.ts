/** 草稿持久化 composable
 *
 * - 写入用 sessionStorage (页面关闭即清, 不跨 tab; 符合"草稿"语义)
 * - 防抖 200ms 写, 避免每个按键触发 IO
 * - 启动时从 storage 读, 与 initial 合并 (initial 优先, 仅在 storage 为空时使用)
 *
 * 用法:
 *   const text = useDraftText(`draft:cust:${sessionId}`, "")
 *   text.value 双向绑定 el-input
 *   text.commit() 发送成功时调用, 清空 storage
 */

import { ref, watch, onScopeDispose } from "vue"

const DEBOUNCE_MS = 200

export function useDraftText(key: string, initial = "") {
  const stored = (() => {
    if (typeof sessionStorage === "undefined") return initial
    try {
      return sessionStorage.getItem(key) ?? initial
    } catch {
      return initial
    }
  })()

  const text = ref(stored)
  let timer: ReturnType<typeof setTimeout> | null = null

  watch(text, (v) => {
    if (typeof sessionStorage === "undefined") return
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => {
      try {
        if (v) sessionStorage.setItem(key, v)
        else sessionStorage.removeItem(key)
      } catch {
        // quota exceeded / disabled storage: silently ignore
      }
    }, DEBOUNCE_MS)
  })

  onScopeDispose(() => {
    if (timer) clearTimeout(timer)
  })

  /** 发送成功后调用, 清空草稿 */
  function commit() {
    text.value = ""
    try {
      if (typeof sessionStorage !== "undefined") sessionStorage.removeItem(key)
    } catch { /* ignore */ }
  }

  return { text, commit }
}
