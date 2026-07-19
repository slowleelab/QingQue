#!/bin/bash
# SmartCS Docker 多服务入口
# 通过 SERVICE 环境变量切换启动目标:
#   SERVICE=bot     → Bot 客服机器人    :8000
#   SERVICE=assist  → Assist 坐席辅助   :8001
#   SERVICE=worker  → Temporal Worker  (无 HTTP)

set -e

case "${SERVICE:-bot}" in
    bot)
        echo "Starting Bot service on :8000"
        exec uvicorn smartcs.main:bot_app --host 0.0.0.0 --port 8000
        ;;
    assist)
        echo "Starting Assist service on :8001"
        exec uvicorn smartcs.main:assist_app --host 0.0.0.0 --port 8001
        ;;
    worker)
        echo "Starting Temporal Worker"
        exec python -m smartcs.workflows.worker
        ;;
    *)
        echo "Unknown SERVICE: ${SERVICE}"
        echo "Valid: bot, assist, worker"
        exit 1
        ;;
esac
