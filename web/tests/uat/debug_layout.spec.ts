import { test } from "@playwright/test"
test("debug 你好 气泡", async ({ page }) => {
  await page.goto("http://localhost:8080")
  await page.waitForLoadState("networkidle")
  const inputs = page.locator("input")
  await inputs.first().fill("debug")
  await inputs.last().fill("测试")
  await page.locator("button:has-text('开始咨询')").click()
  await page.waitForTimeout(500)
  await page.locator("textarea").fill("你好")
  await page.locator("button:has-text('发送')").click()
  await page.waitForTimeout(4000)
  // Debug: check bubble HTML and CSS
  const bubble = page.locator(".msg-bubble").last()
  const html = await bubble.innerHTML()
  const style = await bubble.evaluate(el => {
    const cs = getComputedStyle(el)
    return `width:${cs.width} max-width:${cs.maxWidth} word-break:${cs.wordBreak} white-space:${cs.whiteSpace} display:${cs.display}`
  })
  const parentStyle = await page.locator(".msg-row").last().evaluate(el => {
    const cs = getComputedStyle(el)
    return `display:${cs.display} flex-direction:${cs.flexDirection} width:${cs.width}`
  })
  console.log("Bubble HTML:", html)
  console.log("Bubble CSS:", style)
  console.log("Parent CSS:", parentStyle)
  console.log("Page body text:", (await page.locator("body").innerText()).substring(0, 200))
})
