// 一次性截图脚本: 浅/深各跑一次, 给评审对比布局
// 用法: node scripts/screenshot.mjs
import { chromium } from "@playwright/test"
import { mkdirSync } from "node:fs"
import { join } from "node:path"

const BASE = process.env.BASE_URL || "http://localhost:5173"
const OUT = join(process.cwd(), "tests/uat/screenshots")
mkdirSync(OUT, { recursive: true })

const ROUTES = [
  { path: "/",              name: "01_login" },
  { path: "/agent",         name: "02_agent_empty" },
  { path: "/admin/faqs",    name: "03_faq_manager" },
  { path: "/admin/ingestion", name: "04_ingestion_monitor" },
]

const browser = await chromium.launch({ headless: true })

for (const mode of ["light", "dark"]) {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    colorScheme: mode,
  })
  const page = await ctx.newPage()
  // 强制 data-theme 提前注入 (useDark 写 localStorage 后才生效)
  await page.addInitScript((m) => {
    try { localStorage.setItem("lumio-color-mode", m === "dark" ? "dark" : "light") } catch {}
  }, mode)

  for (const r of ROUTES) {
    try {
      await page.goto(BASE + r.path, { waitUntil: "networkidle", timeout: 15000 })
      await page.waitForTimeout(900)
      const out = join(OUT, `${r.name}_${mode}.png`)
      await page.screenshot({ path: out, fullPage: false })
      console.log(`OK  ${r.name}_${mode}.png`)
    } catch (e) {
      console.warn(`SKIP ${r.name}_${mode}: ${e.message?.slice(0, 80) || e}`)
    }
  }
  await ctx.close()
}

await browser.close()
console.log("done -> " + OUT)
