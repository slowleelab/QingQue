import { test, expect } from "@playwright/test"

const BASE = "http://localhost:8080"

async function loginAs(page: any, id: string, name: string) {
  await page.goto(BASE)
  await page.waitForLoadState("networkidle")
  await expect(page.locator("text=开始咨询")).toBeVisible({ timeout: 10000 })
  const inputs = page.locator("input")
  await inputs.first().fill(id)
  await inputs.last().fill(name)
  await page.locator("button:has-text('开始咨询')").click()
  await expect(page.locator("textarea")).toBeVisible({ timeout: 5000 })
}

test.describe("客户聊天 - 功能测试", () => {

  test("登录后显示欢迎 + 侧边栏", async ({ page }) => {
    await loginAs(page, "t1", "测试用户")
    await expect(page.locator(".msg-bubble").first()).toBeVisible()
    await expect(page.locator("text=会话信息")).toBeVisible()
    await expect(page.locator("text=会话信息")).toBeVisible()
  })

  test("发送消息后收到回复", async ({ page }) => {
    await loginAs(page, "t2", "对话测试")
    const ta = page.locator("textarea")
    await ta.fill("你好")
    await page.locator("button:has-text('发送')").click()
    await expect(ta).toHaveValue("", { timeout: 2000 })
    // 等待 Bot 回复
    await page.waitForTimeout(5000)
    // 应有至少 1 条 bot/system 回复
    const replies = page.locator(".msg-bubble")
    const count = await replies.count()
    expect(count).toBeGreaterThanOrEqual(2) // welcome + reply
  })

  test("转人工触发", async ({ page }) => {
    await loginAs(page, "t3", "转接测试")
    await page.locator("textarea").fill("转人工")
    await page.locator("button:has-text('发送')").click()
    await page.waitForTimeout(5000)
    // 应有系统消息
    const sysMsg = page.locator(".msg-row.system")
    await expect(sysMsg.first()).toBeVisible({ timeout: 5000 })
  })

  test("空消息: 输入纯空格按钮应 disabled", async ({ page }) => {
    await loginAs(page, "t4", "空格测试")
    const ta = page.locator("textarea")
    // 清空已有内容，输入空格
    await ta.fill("")
    await ta.fill("   ")
    // Element Plus el-button disabled 状态下应该有 is-disabled class 或 disabled 属性
    const btn = page.locator("button:has-text('发送')")
    const disabled = await btn.getAttribute("disabled")
    const cls = await btn.getAttribute("class")
    expect(disabled !== null || cls?.includes("is-disabled")).toBeTruthy()
  })
})
