// 一次性截图脚本: 给当前 dev 服务截图存档
// 用法: node scripts/screenshot.mjs
import { chromium } from "@playwright/test"
import { mkdirSync } from "node:fs"
import { join } from "node:path"

const BASE = process.env.BASE_URL || "http://localhost:5173"
const OUT = join(process.cwd(), "tests/uat/screenshots")
mkdirSync(OUT, { recursive: true })

const ROUTES = [
  { path: "/",          name: "01_login" },
  { path: "/agent",     name: "02_agent_empty" },
  { path: "/admin/faqs", name: "03_faq_manager" },
  { path: "/admin/ingestion", name: "04_ingestion_monitor" },
]

const browser = await chromium.launch({ headless: true })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await ctx.newPage()

for (const r of ROUTES) {
  try {
    await page.goto(BASE + r.path, { waitUntil: "networkidle", timeout: 15000 })
    await page.waitForTimeout(800)
    const out = join(OUT, `${r.name}.png`)
    await page.screenshot({ path: out, fullPage: false })
    console.log(`OK  ${r.name}.png`)
  } catch (e) {
    console.warn(`SKIP ${r.name}: ${e.message?.slice(0, 80) || e}`)
  }
}

await browser.close()
console.log("done -> " + OUT)
