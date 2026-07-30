import { client } from "./client"

export interface LoginRequest {
  username: string
  password: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  username: string
  role: string
  display_name?: string | null
}

export function login(data: LoginRequest): Promise<LoginResponse> {
  return client.post("/auth/login", data)
}
