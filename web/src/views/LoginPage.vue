<template>
  <div class="login-container">
    <div class="login-card">
      <h2>SmartCS 管理端</h2>
      <p class="subtitle">银行信用卡智能客服平台</p>
      <el-form :model="form" label-width="0" @submit.prevent="doLogin">
        <el-form-item>
          <el-input v-model="form.username" placeholder="用户名" size="large" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="form.password" type="password" placeholder="密码" size="large" show-password />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" size="large" :loading="loading" native-type="submit" style="width: 100%">
            {{ loading ? "登录中..." : "登 录" }}
          </el-button>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from "vue"
import { useRouter } from "vue-router"
import { ElMessage } from "element-plus"
import { login } from "@/api/auth"
import { setToken } from "@/api/client"

const router = useRouter()
const loading = ref(false)
const form = reactive({ username: "admin", password: "" })

async function doLogin() {
  if (!form.username) {
    ElMessage.warning("请输入用户名")
    return
  }
  loading.value = true
  try {
    const res = await login({ user_id: form.username, password: form.password })
    setToken(res.access_token)
    ElMessage.success(`欢迎，${res.user_id}`)
    router.push("/admin")
  } catch {
    // handled by interceptor
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-container {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
  background: #1d1e2c;
}
.login-card {
  width: 380px;
  padding: 40px;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.3);
}
.login-card h2 {
  text-align: center;
  margin: 0 0 4px;
  color: #303133;
}
.subtitle {
  text-align: center;
  color: #909399;
  font-size: 13px;
  margin: 0 0 28px;
}
</style>
