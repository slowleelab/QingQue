# 灵智（Lumio）性能基准报告

> 测试环境：Apple M4 / 16GB RAM / Python 3.11 / macOS
> 测试日期：2026-07-29
> 版本：v0.2.0

## 微基准（纯计算路径）

不依赖外部服务（DB/Redis/LLM），测量核心算法的计算延迟。

| 路径 | p50 | p95 | p99 | 说明 |
|------|-----|-----|-----|------|
| D1 服务评估 | 0.7μs | 0.8μs | 0.8μs | 意图置信度阈值判断 |
| D2 营销评估 | 0.8μs | 0.8μs | 0.9μs | 情绪+置信度+压制综合判断 |
| D3 风控评估 | 0.2μs | 0.3μs | 0.3μs | 始终激活（无计算） |
| 场景检测 | 0.8-2.8μs | 0.9-2.9μs | 1.0-2.9μs | 关键词+intent 融合，含否定过滤 |
| 意图分类（规则快路） | 138μs | 250μs | 321μs | 正则+规则匹配（不含 LLM） |

**结论**：评估-决策链路的纯计算开销在 **微秒级**，不是性能瓶颈。端到端延迟主要取决于 LLM 推理和网络 I/O。

### 如何复现

```bash
make bench-micro         # 等价于 poetry run python scripts/bench_micro.py
```

输出示例（Apple M4，Python 3.11）：

```
灵智（Lumio）微基准测试 (Apple M4, Python 3.11)
  D1 服务评估       p50=0.7μs  p95=0.8μs  p99=0.8μs
  D2 营销评估       p50=0.8μs  p95=0.8μs  p99=0.9μs
  D3 风控评估       p50=0.2μs  p95=0.3μs  p99=0.3μs
  场景检测         p50=1.2μs  p95=2.8μs  p99=2.9μs
  意图分类(规则)    p50=138μs  p95=250μs  p99=321μs
```

## 负载测试（Locust）

### 前置条件

```bash
# 1. 启动完整中间件栈
make up && make init

# 2. 启动服务
make dev

# 3. 安装 locust
pip install locust
```

### 运行

```bash
# Web UI 模式（实时图表）
locust -f scripts/locustfile.py --host=http://localhost:8000

# 无 UI 模式（CI 用）
locust -f scripts/locustfile.py --host=http://localhost:8000 \
  --headless -u 50 -r 5 -t 60s --csv=results/bench
```

> Locust 用户行为类 `LumioBotUser` 模拟客户完整对话流程（send → poll）。

### 测试场景

- **`LumioBotUser`**：模拟客户完整对话流程（`POST /api/chat/send` → `GET /api/chat/poll`）
- 问题池：12 条覆盖常见信用卡咨询意图
- `wait_time`：1-5s 随机间隔（模拟真实用户行为）
- 辅助任务：健康检查（`GET /api/health`，weight=2）

### 关键指标（预期）

| 指标 | 目标 | 说明 |
|------|------|------|
| `POST /api/chat/send` p50 | < 50ms | 消息入队（Redis 写） |
| `GET /api/chat/poll` p50 | < 3s | 含意图分类 + RAG 检索 + LLM 生成 |
| `GET /api/chat/poll` p99 | < 10s | 含降级场景 |
| `GET /api/health` p50 | < 5ms | 健康检查 |
| 错误率 | < 1% | 超时/异常比例 |

### 结果记录

每次压测后，将结果追加到本节（最新在上）：

| 日期 | 并发 | RPS | send p50 | send p99 | poll p50 | poll p99 | 错误率 | 备注 |
|------|------|-----|----------|----------|----------|----------|--------|------|
| - | - | - | - | - | - | - | - | 待首次压测 |

> 当前基准数据为早期 e2e 压测结果，可在 `make bench` 走通后更新。

## 性能优化方向

1. **LLM 推理**：当前 Ollama 本地推理是最大延迟来源（p50 ~2s）。生产环境可用 vLLM/TGI 替换，预期 p50 < 500ms。
2. **RAG 检索**：BM25 + 向量并行已是优化后的架构，进一步可用 ES 缓存层。
3. **Redis 队列**：当前单 worker 消费，可通过增加 worker 数提升吞吐。
4. **连接池**：PostgreSQL/Redis 连接池大小可通过 `POSTGRES_*` / `REDIS_*` 配置调优。
5. **熔断器状态机**：四级熔断（closed → half_open → open → forced_open）状态切换耗时 < 1ms，可忽略。

## 相关文档

- [架构文档](./architecture.md) — 数据流与状态机
- [配置参考](./configuration.md) — 调优相关环境变量
