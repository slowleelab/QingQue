import { client } from "./client"

export interface LoginRequest {
  user_id: string
  password?: string
  role?: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  user_id: string
  role: string
}

export function login(data: LoginRequest): Promise<LoginResponse> {
  return client.post("/auth/login", { ...data, role: "admin" })
}
