import { ref, onUnmounted, onMounted, type Ref } from "vue"

export interface UseIngestionMonitorOptions {
  /** 主加载函数, 父组件实现 (listDocuments 等) */
  loader: () => Promise<void>
  /** 轮询间隔 (ms), 默认 3000 */
  intervalMs?: number
  /** 起始自动刷新, 默认 true */
  autoStart?: boolean
  /** 切到后台 tab 是否暂停, 默认 true */
  pauseOnHidden?: boolean
}

export interface IngestionMonitorControls {
  isPolling: Ref<boolean>
  loading: Ref<boolean>
  /** 最后一次数据加载耗时 (ms), -1 表示无 */
  elapsedMs: Ref<number>
  start: () => void
  stop: () => void
  /** 立刻跑一次 loader (不重启 timer) */
  refresh: () => void
}

/** IngestionMonitor 专用监控 composable
 *
 *  - 自动轮询 + 切 tab 暂停 + 组件卸载停
 *  - 暴露 loading / isPolling / elapsedMs 三个 ref 供 UI 反馈
 *  - loader 抛错时被吞, 不影响下次 tick
 */
export function useIngestionMonitor(opts: UseIngestionMonitorOptions): IngestionMonitorControls {
  const isPolling = ref(false)
  const loading = ref(false)
  const elapsedMs = ref(-1)
  let timer: ReturnType<typeof setInterval> | null = null

  async function tick() {
    if (opts.pauseOnHidden !== false && document.visibilityState !== "visible") return
    const t0 = performance.now()
    loading.value = true
    try {
      await opts.loader()
    } catch {
      /* handled by caller / interceptor */
    } finally {
      elapsedMs.value = Math.round(performance.now() - t0)
      loading.value = false
    }
  }

  function start() {
    stop()
    isPolling.value = true
    timer = setInterval(tick, opts.intervalMs ?? 3000)
  }

  function stop() {
    if (timer) { clearInterval(timer); timer = null }
    isPolling.value = false
  }

  function refresh() { tick() }

  onMounted(() => {
    refresh()
    if (opts.autoStart !== false) start()
  })
  onUnmounted(stop)

  return { isPolling, loading, elapsedMs, start, stop, refresh }
}
