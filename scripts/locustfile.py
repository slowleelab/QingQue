"""SmartCS 性能压测脚本（Locust）

压测 Bot 对话管道：send → poll（模拟客户咨询完整流程）

用法:
    pip install locust
    # 先启动 Bot 服务
    make dev
    # 运行压测（Web UI）
    locust -f scripts/locustfile.py --host=http://localhost:8000
    # 无 UI 模式（60s, 50 并发）
    locust -f scripts/locustfile.py --host=http://localhost:8000 --headless -u 50 -r 5 -t 60s
"""

from locust import HttpUser, between, task


# 压测用问题池（覆盖常见意图）
_QUESTIONS = [
    "信用卡年费怎么减免",
    "这个月账单什么时候出",
    "我的额度能提升吗",
    "分期手续费多少",
    "积分怎么兑换",
    "挂失后多久能补卡",
    "最低还款额是什么意思",
    "怎么修改账单地址",
    "境外消费有手续费吗",
    "临时额度怎么申请",
    "账单分期和消费分期有什么区别",
    "信用卡逾期了怎么办",
]


class SmartCSBotUser(HttpUser):
    """模拟客户使用 Bot 对话服务"""

    wait_time = between(1, 5)

    @task
    def chat_send_and_poll(self):
        """完整对话流程：发送消息 → 轮询获取回复"""
        import random

        question = random.choice(_QUESTIONS)

        # 1. 发送消息
        with self.client.post(
            "/api/chat/send",
            json={"message": question},
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"send failed: {resp.status_code}")
                return
            data = resp.json()
            if not data.get("accepted"):
                resp.failure("send not accepted")
                return
            session_id = data.get("session_id")
            if not session_id:
                resp.failure("no session_id in response")
                return
            resp.success()

        # 2. 轮询获取回复（最多 30s）
        with self.client.get(
            "/api/chat/poll",
            params={"session_id": session_id, "timeout": 30},
            catch_response=True,
        ) as poll_resp:
            if poll_resp.status_code != 200:
                poll_resp.failure(f"poll failed: {poll_resp.status_code}")
                return
            data = poll_resp.json()
            status = data.get("status")
            if status == "done":
                poll_resp.success()
            elif status == "timeout":
                poll_resp.failure("poll timeout")
            else:
                poll_resp.failure(f"unexpected status: {status}")

    @task(weight=2)
    def health_check(self):
        """健康检查（高频）"""
        self.client.get("/api/health")
