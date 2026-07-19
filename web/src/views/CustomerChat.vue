<template>
  <div class="customer-app">
    <div v-if="!loggedIn" class="login-overlay">
      <div class="login-card">
        <h2><el-icon :size="24"><UserFilled /></el-icon> 客户登录</h2>
        <el-input v-model="customerId" placeholder="客户ID" size="large" />
        <el-input v-model="customerName" placeholder="您的名称" size="large" />
        <el-button type="primary" size="large" @click="login" :disabled="!customerId.trim() || !customerName.trim()">
          开始咨询
        </el-button>
      </div>
    </div>

    <div v-else class="chat-layout">
      <header class="chat-header">
        <span class="logo">💬 智能客服</span>
        <span class="status" :class="{ connected: isConnected }">
          {{ isConnected ? '已接通' : isInQueue ? `排队中(${queuePos}位)` : '机器人服务中' }}
        </span>
        <span class="user">{{ customerName }}</span>
      </header>

      <div class="chat-body">
        <div class="message-list" ref="msgList">
          <div v-for="msg in msgs" :key="msg.id" class="msg-row" :class="msg.role">
            <div class="msg-wrapper">
              <div class="msg-bubble">{{ msg.content }}</div>
              <div class="msg-time">{{ new Date(msg.time).toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'}) }}</div>
            </div>
          </div>
          <div v-if="msgs.length === 0" class="empty-chat">发送消息开始咨询</div>
        </div>
      </div>

      <div class="chat-input-area">
        <el-input
          v-model="inputText"
          type="textarea"
          :rows="2"
          placeholder="输入消息..."
          @keydown.enter.exact.prevent="handleSend"
          :disabled="isInQueue && !isConnected"
        />
        <el-button type="primary" :icon="Promotion" @click="handleSend" :disabled="!inputText.trim()">
          发送
        </el-button>
      </div>

      <aside class="side-panel">
        <div class="panel-title">会话信息</div>
        <div class="info-item"><label>会话ID</label><span>{{ sid || '-' }}</span></div>
        <div class="info-item"><label>状态</label><span>{{ isConnected ? '人工服务中' : isInQueue ? '排队等待' : '机器人服务' }}</span></div>
        <div class="info-item"><label>消息数</label><span>{{ msgs.length }}</span></div>
        <el-button type="danger" size="small" @click="endSession" style="margin-top:12px;width:100%">结束会话</el-button>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, watch, computed } from "vue"
import { UserFilled, Promotion } from "@element-plus/icons-vue"
import { useCustomerChat } from "@/composables/useCustomerChat"

const chat = useCustomerChat()

const loggedIn = ref(false)
const customerId = ref("customer-001")
const customerName = ref("")
const inputText = ref("")
const msgList = ref<HTMLElement>()

const msgs = computed(() => chat.messages.value)
const isConnected = computed(() => chat.connected.value)
const isInQueue = computed(() => chat.inQueue.value)
const queuePos = computed(() => chat.queuePosition.value)
const sid = computed(() => chat.sessionId.value)

function login() {
  if (!customerId.value.trim() || !customerName.value.trim()) return
  loggedIn.value = true
  chat.addMsg("system", `您好 ${customerName.value}，请问有什么可以帮您？`)
}

async function handleSend() {
  if (!inputText.value.trim()) return
  await chat.sendMessage(inputText.value)
  inputText.value = ""
  if (!chat.polling.value) {
    chat.startPolling()
  }
  nextTick(() => scrollBottom())
}

function endSession() {
  chat.clearChat()
  loggedIn.value = false
}

watch(() => msgs.value.length, () => nextTick(() => scrollBottom()))

function scrollBottom() {
  if (msgList.value) msgList.value.scrollTop = msgList.value.scrollHeight
}
</script>

<style scoped>
.customer-app { height: 100vh; display: flex; flex-direction: column; background: #f0f2f5; font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; }
.login-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.5); display: flex; align-items: center; justify-content: center; z-index: 100; }
.login-card { background: #fff; padding: 32px; border-radius: 12px; width: 360px; display: flex; flex-direction: column; gap: 16px; }
.chat-layout { flex: 1; display: grid; grid-template-columns: 1fr 260px; grid-template-rows: 56px 1fr 80px; height: 100vh; }
.chat-header { grid-column: 1/-1; background: linear-gradient(135deg, #1e3a5f, #2563eb); color: #fff; display: flex; align-items: center; padding: 0 20px; gap: 16px; }
.chat-header .logo { font-size: 18px; font-weight: 700; }
.chat-header .status { font-size: 12px; opacity: .8; }
.chat-header .status.connected { color: #4ade80; }
.chat-header .user { margin-left: auto; font-size: 13px; }
.chat-body { overflow: hidden; display: flex; }
.message-list { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
.msg-row { display: flex; width: 100%; }
.msg-row.customer { justify-content: flex-end; }
.msg-row.bot, .msg-row.agent { justify-content: flex-start; }
.msg-row.system { justify-content: center; }
.msg-wrapper { display: flex; flex-direction: column; gap: 2px; max-width: 70%; }
.customer .msg-wrapper { align-items: flex-end; }
.bot .msg-wrapper, .agent .msg-wrapper { align-items: flex-start; }
.system .msg-wrapper { align-items: center; }
.msg-bubble { padding: 10px 14px; border-radius: 12px; font-size: 14px; line-height: 1.5; word-break: break-word; overflow-wrap: break-word; width: fit-content; }
.msg-time { font-size: 11px; color: #94a3b8; white-space: nowrap; }
.system .msg-time { text-align: center; }
.customer .msg-bubble { background: #2563eb; color: #fff; border-bottom-right-radius: 4px; }
.bot .msg-bubble, .agent .msg-bubble { background: #fff; color: #1f2937; border-bottom-left-radius: 4px; box-shadow: 0 1px 2px rgba(0,0,0,.05); }
.system .msg-bubble { background: #fef3c7; color: #92400e; font-size: 12px; border-radius: 8px; }
.empty-chat { text-align: center; color: #9ca3af; margin-top: 40px; }
.chat-input-area { grid-column: 1; padding: 12px 16px; background: #fff; border-top: 1px solid #e5e7eb; display: flex; gap: 10px; align-items: flex-end; }
.side-panel { grid-column: 2; grid-row: 2/4; background: #fff; border-left: 1px solid #e5e7eb; padding: 16px; }
.panel-title { font-size: 15px; font-weight: 700; color: #1e3a5f; margin-bottom: 16px; }
.info-item { margin-bottom: 10px; }
.info-item label { font-size: 12px; color: #6b7280; display: block; }
.info-item span { font-size: 13px; color: #1f2937; }
</style>
