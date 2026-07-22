import axios from "axios"
import { ElMessage } from "element-plus"
import type { ApiError } from "./types"

const TOKEN_KEY = "smartcs_token"

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

const client = axios.create({
  baseURL: "/api",
  timeout: 30000,
  headers: { "Content-Type": "application/json" },
})

// 请求拦截器：自动附加 Bearer token
client.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 错误码 → 中文提示映射
const ERROR_CODE_MAP: Record<string, string> = {
  "2": "输入有误，请检查后重试",
  "3": "业务处理失败",
  "4": "外部服务异常，请稍后重试",
  "5": "系统错误，请联系管理员",
}

client.interceptors.response.use(
  (response) => response.data,
  (error) => {
    // 401 → 跳转登录页
    if (error.response?.status === 401) {
      clearToken()
      if (window.location.pathname !== "/login") {
        window.location.href = "/login"
      }
      return Promise.reject(error)
    }
    const data = error.response?.data as ApiError | undefined
    if (data?.error) {
      const prefix = data.error.code.toString()[0]
      const message = ERROR_CODE_MAP[prefix] ?? "未知错误"
      ElMessage.error(`${message}（${data.error.code}）`)
    } else {
      ElMessage.error(error.message || "网络异常")
    }
    return Promise.reject(error)
  },
)

export { client }
