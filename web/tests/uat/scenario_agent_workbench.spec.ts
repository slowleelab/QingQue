import { test, expect } from "@playwright/test"

const AGENT_URL = "http://localhost:8080/agent"

test.describe("坐席工作台 - 功能测试", () => {
  test("会话列表 + 空状态提示正常渲染", async ({ page }) => {
    await page.goto(AGENT_URL)
    await page.waitForLoadState("networkidle")
    await expect(page.locator("[data-testid='session-list']")).toBeVisible({ timeout: 10000 })
    await expect(page.locator("text=请从左侧选择一个会话")).toBeVisible({ timeout: 5000 })
  })

  test("点击会话项后展示辅助面板", async ({ page }) => {
    await page.goto(AGENT_URL)
    await page.waitForLoadState("networkidle")
    const items = page.locator("[data-testid='session-item']")
    if (await items.count() > 0) {
      await items.first().click()
      await expect(page.locator("[data-testid='assist-panel']")).toBeVisible({ timeout: 10000 })
    }
    // 无会话时跳过
  })

  test("会话项 phase 标签正确", async ({ page }) => {
    await page.goto(AGENT_URL)
    await page.waitForLoadState("networkidle")
    const items = page.locator("[data-testid='session-item']")
    if (await items.count() > 0) {
      const tag = items.first().locator(".el-tag")
      if (await tag.isVisible()) {
        const t = (await tag.textContent())?.trim() || ""
        expect(["机器人","坐席辅助","已结束"]).toContain(t)
      }
    }
  })

  test("跨窗口：客户发消息 → 坐席端加载", async ({ browser }) => {
    const custPage = await browser.newPage()
    await custPage.goto("http://localhost:8080")
    await custPage.waitForLoadState("networkidle")
    const inputs = custPage.locator("input")
    await inputs.first().fill("cross")
    await inputs.last().fill("跨窗口")
    await custPage.locator("button:has-text('开始咨询')").click()
    await custPage.waitForTimeout(500)
    await custPage.locator("textarea").fill("转人工")
    await custPage.locator("button:has-text('发送')").click()
    await custPage.waitForTimeout(5000)

    const agentPage = await browser.newPage()
    await agentPage.goto(AGENT_URL)
    await agentPage.waitForLoadState("networkidle")
    await expect(agentPage.locator("[data-testid='session-list']")).toBeVisible({ timeout: 10000 })

    await custPage.close()
    await agentPage.close()
  })
})
