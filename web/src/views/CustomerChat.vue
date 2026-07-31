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
.customer-app { height: 100vh; display: flex; flex-direction: column; background: var(--color-bg-page); font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; }
.login-overlay { position: fixed; inset: 0; background: var(--color-bg-mask); display: flex; align-items: center; justify-content: center; z-index: 100; }
.login-card { background: var(--color-bg-surface); padding: 32px; border-radius: var(--radius-xl); width: 360px; display: flex; flex-direction: column; gap: var(--space-4); }
.chat-layout { flex: 1; display: grid; grid-template-columns: 1fr 260px; grid-template-rows: 56px 1fr 80px; height: 100vh; }
.chat-header { grid-column: 1/-1; background: var(--color-primary); color: var(--color-text-on-primary); display: flex; align-items: center; padding: 0 20px; gap: var(--space-4); }
.chat-header .logo { font-size: var(--fs-xl); font-weight: 700; }
.chat-header .status { font-size: var(--fs-sm); opacity: .8; }
.chat-header .status.connected { color: var(--color-success); }
.chat-header .user { margin-left: auto; font-size: var(--fs-sm); }
.chat-body { overflow: hidden; display: flex; }
.message-list { flex: 1; overflow-y: auto; padding: var(--space-4); display: flex; flex-direction: column; gap: 10px; background: var(--color-msg-list-bg); }
.msg-row { display: flex; width: 100%; }
.msg-row.customer { justify-content: flex-end; }
.msg-row.bot, .msg-row.agent { justify-content: flex-start; }
.msg-row.system { justify-content: center; }
.msg-wrapper { display: flex; flex-direction: column; gap: 2px; max-width: 70%; }
.customer .msg-wrapper { align-items: flex-end; }
.bot .msg-wrapper, .agent .msg-wrapper { align-items: flex-start; }
.system .msg-wrapper { align-items: center; }
.msg-bubble { padding: 10px 14px; border-radius: var(--radius-xl); font-size: var(--fs-base); line-height: 1.5; word-break: break-word; overflow-wrap: break-word; width: fit-content; }
.msg-time { font-size: var(--fs-xs); color: var(--color-text-placeholder); white-space: nowrap; }
.system .msg-time { text-align: center; }
.customer .msg-bubble { background: var(--color-user-bg); color: var(--color-user-text); border-bottom-right-radius: var(--radius-sm); }
.bot .msg-bubble, .agent .msg-bubble { background: var(--color-bot-bg); color: var(--color-bot-text); border-bottom-left-radius: var(--radius-sm); box-shadow: var(--shadow-sm); }
.system .msg-bubble { background: var(--color-bg-hover); color: var(--color-text-regular); font-size: var(--fs-sm); border-radius: var(--radius-md); }
.empty-chat { text-align: center; color: var(--color-text-secondary); margin-top: 40px; }
.chat-input-area { grid-column: 1; padding: var(--space-3) var(--space-4); background: var(--color-bg-surface); border-top: 1px solid var(--color-border-lighter); display: flex; gap: 10px; align-items: flex-end; }
.side-panel { grid-column: 2; grid-row: 2/4; background: var(--color-bg-surface); border-left: 1px solid var(--color-border-lighter); padding: var(--space-4); }
.panel-title { font-size: var(--fs-md); font-weight: 700; color: var(--color-text-primary); margin-bottom: var(--space-4); }
.info-item { margin-bottom: 10px; }
.info-item label { font-size: var(--fs-sm); color: var(--color-text-secondary); display: block; }
.info-item span { font-size: var(--fs-sm); color: var(--color-text-primary); }
</style>
