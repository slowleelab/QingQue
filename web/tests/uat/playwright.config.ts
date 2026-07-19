import { defineConfig } from "@playwright/test"

export default defineConfig({
  testDir: ".",
  timeout: 120000,
  expect: { timeout: 15000 },
  retries: 1,
  reporter: [
    ["html", { outputFolder: "playwright-report" }],
    ["list"],
  ],
  use: {
    baseURL: "http://localhost:8080",
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "chrome", use: { browserName: "chromium", channel: "chrome" } },
  ],
  // 可选: 自动启动服务 (需要先安装依赖)
  // webServer: [
  //   { command: "cd ../../agent && poetry run uvicorn smartcs.main:bot_app --port 8000", port: 8000, reuseExistingServer: true },
  //   { command: "cd ../../agent && poetry run uvicorn smartcs.main:assist_app --port 8001", port: 8001, reuseExistingServer: true },
  // ],
})
