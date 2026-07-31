import { useDark, useToggle } from "@vueuse/core"

/** 全局暗色模式开关
 *
 *  - useDark: 跟随系统 (prefers-color-scheme), 写到 localStorage, 切到 <html data-theme>
 *  - selector 必填, 否则 useDark 不挂载 (默认是 class)
 *  - valueDark/valueLight 必填, 配合 _tokens.scss 中的 [data-theme="dark"]
 */
export function useColorMode() {
  const isDark = useDark({
    selector: "html",
    attribute: "data-theme",
    valueDark: "dark",
    valueLight: "light",
    storageKey: "lumio-color-mode",
  })
  const toggle = useToggle(isDark)

  return { isDark, toggle }
}
