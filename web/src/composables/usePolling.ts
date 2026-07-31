/** 通用轮询 composable
 *
 * 替代前端 5 处近乎相同的 setInterval 长轮询模板:
 * - stores/assist.ts (chat-svc 会话列表)
 * - stores/chat.ts (chat-svc 消息)
 * - ConversationPanel.vue (chat-svc 消息)
 * - useCustomerChat.ts (bot poll)
 * - useCustomerChat.ts (chat-svc poll)
 *
 * 特性:
 * - url: 支持 string / Ref<string>, url 变化时自动重启
 * - interval: 间隔 ms, 默认 5000
 * - since: 可选时间戳游标, 拼到 ?since=N, 数据回填后由调用方维护游标
 * - onData: 数据回调, 返回 void (不阻塞下次轮询)
 * - onError: 错误回调, 不抛
 * - immediate: 是否立即触发一次, 默认 true
 * - pauseOnHidden: document hidden 时暂停, 默认 true
 *
 * 返回: { start, stop, restart, isPolling }
 *
 * 注意: 本 hook 仅做"拉取 + 回调", 不做 axios 包装 (chat-svc poll 路径不固定);
 * 401 错误处理由 api/chat-svc.ts 包装层 (B2) 接管.
 */

import { ref, watch, onScopeDispose, unref, type MaybeRef, type Ref } from "vue"

export interface UsePollingOptions<T> {
  interval?: number
  since?: Ref<number | null | undefined>
  onData: (data: T) => void
  onError?: (e: unknown) => void
  immediate?: boolean
  pauseOnHidden?: boolean
}

export function usePolling<T = unknown>(
  url: MaybeRef<string | null | undefined>,
  opts: UsePollingOptions<T>,
) {
  const isPolling = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null
  let stopped = true
  let inFlight = false

  const interval = opts.interval ?? 5000
  const immediate = opts.immediate ?? true
  const pauseOnHidden = opts.pauseOnHidden ?? true

  function clearTimer() {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  function schedule() {
    clearTimer()
    if (stopped) return
    timer = setTimeout(tick, interval)
  }

  async function tick() {
    if (stopped) return
    if (pauseOnHidden && typeof document !== "undefined" && document.visibilityState !== "visible") {
      schedule()
      return
    }
    const target = unref(url)
    if (!target) {
      schedule()
      return
    }
    inFlight = true
    isPolling.value = true
    try {
      const finalUrl = appendSince(target, opts.since?.value)
      const resp = await fetch(finalUrl)
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const data = (await resp.json()) as T
      opts.onData(data)
    } catch (e) {
      opts.onError?.(e)
    } finally {
      inFlight = false
      isPolling.value = false
      schedule()
    }
  }

  function start() {
    if (!stopped) return
    stopped = false
    isPolling.value = true
    if (immediate) {
      tick()
    } else {
      schedule()
    }
  }

  function stop() {
    stopped = true
    clearTimer()
    isPolling.value = false
  }

  function restart() {
    stop()
    start()
  }

  // url 变化时自动重启 (例如切换 sessionId)
  watch(
    () => unref(url),
    (newUrl, oldUrl) => {
      if (newUrl === oldUrl) return
      if (newUrl && !stopped) restart()
    },
  )

  onScopeDispose(stop)

  return { start, stop, restart, isPolling, inFlight: () => inFlight }
}

function appendSince(url: string, since: number | null | undefined): string {
  if (since == null) return url
  const sep = url.includes("?") ? "&" : "?"
  return `${url}${sep}since=${encodeURIComponent(since)}`
}
