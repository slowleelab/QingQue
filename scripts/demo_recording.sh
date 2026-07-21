#!/usr/bin/env bash
# SmartCS 一键 Demo 演示脚本（用于录制 README 首屏 GIF）
# 用法: 先 `make demo`，待 bot/assist healthy 后运行本脚本。
set -uo pipefail

BOT="http://localhost:8000"

# 打印一条命令（$ 前缀 + 高亮感），稍作停顿模拟输入
show_cmd() {
  printf '\n\033[1;32m$\033[0m \033[1m%s\033[0m\n' "$1"
  sleep 0.7
}

# 发送咨询并返回 session_id；失败时重试，避免瞬时抖动毁掉录制
send_chat() {
  local q="$1" sid="" i
  for i in 1 2 3 4 5; do
    sid=$(curl -s -X POST "$BOT/api/chat/send" \
      -H 'Content-Type: application/json' \
      -d "{\"message\":\"$q\"}" | jq -r '.session_id // empty')
    [ -n "$sid" ] && { printf '%s' "$sid"; return 0; }
    sleep 1
  done
  return 1
}

printf '╭────────────────────────────────────────────╮\n'
printf '│  SmartCS · 银行信用卡智能客服 · 一键 Demo   │\n'
printf '╰────────────────────────────────────────────╯\n'
sleep 1

printf '\n\033[36m① 一条命令拉起完整系统（中间件+迁移+知识库+Bot/Assist）\033[0m\n'
show_cmd "make demo"
cat <<'EOF'
[+] Running 16/16
 ✔ demo-init       Exited (0)   数据库迁移 ✓  种子知识库 ✓
 ✔ smartcs-bot     Healthy      → http://localhost:8000
 ✔ smartcs-assist  Healthy      → http://localhost:8001
✅ Demo 已启动
EOF
sleep 1.2

printf '\n\033[36m② 发送一条客户咨询\033[0m\n'
show_cmd "curl -X POST $BOT/api/chat/send -d '{\"message\":\"信用卡年费怎么减免\"}'"
SID=$(send_chat "信用卡年费怎么减免")
if [ -z "$SID" ]; then
  echo '(服务暂不可用，请确认 make demo 已就绪后重试)'
  exit 1
fi
printf '{"accepted":true,"session_id":"%s"}\n' "$SID"
sleep 1

printf '\n\033[36m③ 获取智能回复（意图识别 + RAG 检索 + LLM 生成）\033[0m\n'
show_cmd "curl $BOT/api/chat/poll?session_id=$SID | jq"
curl -s "$BOT/api/chat/poll?session_id=$SID&timeout=20" | jq '{status,intent,confidence,source,reply}'
sleep 6

printf '\n╭────────────────────────────────────────────╮\n'
printf '│ ✅ 体验完成 · make demo-down 停止 · 欢迎 Star │\n'
printf '╰────────────────────────────────────────────╯\n'
sleep 3
