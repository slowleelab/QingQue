# SmartCS MCP Server（银行信用卡智能客服工具服务）

基于 **Spring Boot 3.4 + Spring AI 1.0（MCP Server WebMVC starter）** 的独立工程，通过
[Model Context Protocol](https://modelcontextprotocol.io/) 对外暴露 **22 个信用卡业务工具**，
供上游编排大脑（SmartCS 的 Python Bot / Assist 服务，经 Higress AI 网关）以标准 MCP 协议调用。

> ⚠️ **安全红线**：本工程为参考 / mock 实现，所有数据均来自内存 Mock 仓库，**不连接任何真实银行核心系统**。
> 工具入参采用天然业务字段 **完整卡号 `cardNo`**，但均为 **Luhn 合法的假卡号**（如 `6225880012346780`），
> 绝不涉及真实持卡人信息、CVV、密码等敏感要素。敏感（写类）工具的用户确认、授权与额度校验由上游
> Python 编排层（确认状态机 + ToolGuard）与网关统一治理。

## 工具清单（12 只读 + 10 敏感写）

按业务域分组。**敏感·写**类工具会改动账户状态，需上游 Python 确认状态机放行后执行，
并支持可选入参 `idempotencyKey` 做幂等去重。

### 账单 / 年费

| # | 工具名 | 类型 | 说明 |
|---|--------|------|------|
| 1 | `query_card_bill` | 只读 | 当期账单概览（应还 / 最低还款 / 账单日 / 到期日 / 尚需偿还） |
| 2 | `query_bill_detail` | 只读 | 指定账单周期（yyyy-MM）的交易明细 |
| 3 | `query_annual_fee` | 只读 | 年费政策：金额 / 减免门槛（刷卡笔数或金额）/ 是否已减免 |

### 交易

| # | 工具名 | 类型 | 说明 |
|---|--------|------|------|
| 4 | `query_transactions` | 只读 | 近期交易流水（可按起止日期筛选） |
| 5 | `report_transaction_dispute` | **敏感·写** | 发起交易争议（对指定交易流水号申诉） |

### 额度

| # | 工具名 | 类型 | 说明 |
|---|--------|------|------|
| 6 | `query_credit_limit` | 只读 | 固定额度 / 已用 / 可用 / 临时额度 |
| 7 | `query_limit_adjust_history` | 只读 | 历史提 / 降额记录（临时 / 永久） |
| 8 | `adjust_temp_credit_limit` | **敏感·写** | 临时额度调整（提额） |
| 9 | `apply_permanent_limit` | **敏感·写** | 申请永久提额 |

### 分期

| # | 工具名 | 类型 | 说明 |
|---|--------|------|------|
| 10 | `query_installment_offer` | 只读 | 账单分期可选方案（3/6/12/24 期费率与每期金额） |
| 11 | `query_installment_status` | 只读 | 现有分期计划状态（进行中 / 已结清 / 已取消） |
| 12 | `apply_bill_installment` | **敏感·写** | 办理账单分期 |
| 13 | `cancel_installment` | **敏感·写** | 取消指定分期计划 |

### 还款

| # | 工具名 | 类型 | 说明 |
|---|--------|------|------|
| 14 | `query_repayment_history` | 只读 | 历史还款记录 |
| 15 | `repay_credit_card` | **敏感·写** | 信用卡还款 |
| 16 | `set_auto_repay` | **敏感·写** | 开通 / 关闭自动还款（全额 / 最低还款额，指定扣款渠道） |

### 积分 / 权益

| # | 工具名 | 类型 | 说明 |
|---|--------|------|------|
| 17 | `query_points` | 只读 | 积分余额与即将到期积分 |
| 18 | `query_card_benefits` | 只读 | 卡片等级与权益清单 |
| 19 | `redeem_points` | **敏感·写** | 积分兑换（消耗积分兑换指定权益 / 商品） |

### 卡片服务

| # | 工具名 | 类型 | 说明 |
|---|--------|------|------|
| 20 | `query_card_status` | 只读 | 卡片状态（正常 / 未激活 / 冻结 / 挂失） |
| 21 | `activate_card` | **敏感·写** | 卡片激活 |
| 22 | `report_card_lost` | **敏感·写** | 卡片挂失 / 临时冻结 |

10 个敏感（写）工具的名称由上游 Python `MCP_SENSITIVE_TOOLS` 白名单管控（与工具语义取并集），
开启 `MCP_ENABLED=true` 后 Python 侧确认状态机即刻对其生效。

### 入参约定：`cardNo`（完整卡号）

所有工具以 `cardNo` 作为账户主键：**13–19 位纯数字**，例如 `6225880012346780`。
`ToolSupport.requireCardNo` 负责入参格式校验；Luhn 校验为可配置开关（默认关，见下）。
4 个写工具额外支持可选入参 `idempotencyKey`——用于幂等去重（同 key 重复提交回放首次结果，不重复受理）。

## 分层架构（六边形 / 端口-适配器）

领域逻辑与基础设施解耦，mock 适配器可整体替换为真实核心系统适配器而不动领域层：

```
com.smartcs.mcp
├── McpServerApplication              # 入口（@ConfigurationPropertiesScan）
├── config/
│   ├── CreditCardTool                # 空标记接口：所有 @Tool 服务类实现它
│   ├── ToolConfiguration             # 注入 List<CreditCardTool> 自动收集 → 单一 ToolCallbackProvider
│   ├── CreditCardProperties          # smartcs.creditcard.*（费率/提额倍数/期数/渠道/幂等TTL/Luhn）
│   ├── SecurityProperties            # smartcs.security.api-key.*（默认关）
│   └── ApiKeyAuthFilter              # 可选 API-Key 过滤器（@ConditionalOnProperty，默认不注册）
├── domain/
│   ├── CardAccount / TransactionRecord           # 账户聚合与交易实体（主键 cardNo）
│   ├── port/CardAccountRepository / IdempotencyStore  # 出站端口（接口）
│   ├── exception/ErrorCode / BusinessException   # 错误码体系 + 面向用户中文消息
│   ├── support/Ids                                # 受理单号生成、日志尾号
│   └── service/                                   # 领域服务（账单/额度/分期/还款/积分/卡片）+ IdempotentExecutor
├── adapter/mock/
│   ├── InMemoryCardAccountRepository # 每账户 ReentrantLock 原子变更
│   ├── InMemoryIdempotencyStore      # ConcurrentHashMap + TTL
│   ├── DemoCards / MockDataSeeder    # @PostConstruct 载入两张演示卡
├── tools/                            # 薄适配层：@Tool 校验入参后委派领域服务
├── observability/ToolCallAspect      # AOP 环绕 @Tool → Micrometer 计数/计时 + MDC callId
└── nacos/NacosRegistrationService    # 可选：nacos profile 下注册实例
```

> **新增工具（自动注册）**：新建一个 `@Tool` 方法所在的服务类并 `implements CreditCardTool` 即可——
> `ToolConfiguration` 通过注入 `List<CreditCardTool>` 自动收集全部实现类，无需再手工维护 Bean 列表。

## 金融正确性

- **并发安全**：`CardAccountRepository.updateAtomically(cardNo, fn)` 持每账户 `ReentrantLock`，
  消除还款 / 提额 / 分期的 in-place 竞态。
- **幂等**：写工具传入 `idempotencyKey` 时，`IdempotentExecutor` 命中缓存回放原结果、不重复受理；
  未传 key 保持直接执行语义。TTL 由 `smartcs.creditcard.idempotency-ttl` 控制（默认 30 分钟）。
- **错误码体系**：校验 / 业务失败统一抛 `BusinessException(ErrorCode, 中文消息)`，Spring AI 将消息
  作为工具错误结果回传；错误码仅用于日志 / 指标维度，不泄漏内部规则。

## 演示数据

内存中预置三张 **Luhn 合法的演示假卡**（`MockDataSeeder`）：

- 主卡 `6225880012346780`（白金卡）：固定额度 50000，已用 18650，本期应还 8650，积分 28560（即将到期 3200），6 笔流水；含 1 个进行中分期计划、还款/提额历史、年费政策与权益
- 副卡 `6225880000001231`（金卡）：固定额度 20000，临时额度 5000，本期已还清（3200），积分 6120，3 笔流水；年费已减免
- 新卡 `6225880000007899`（未激活）：固定额度 10000，用于演示 `query_card_status` / `activate_card`

## 配置项

| 前缀 | 关键项 | 默认 | 说明 |
|------|--------|------|------|
| `smartcs.creditcard` | `installment-fee-rates` | 3/6→0.0060，12→0.0066，24→0.0072 | 分期手续费率表 |
| `smartcs.creditcard` | `temp-limit-multiplier` | 2.0 | 临时提额上限 = 固定额度 × 倍数 |
| `smartcs.creditcard` | `default-repay-channel` | 本人储蓄卡快捷 | 还款默认渠道 |
| `smartcs.creditcard` | `idempotency-ttl` | 30m | 幂等结果缓存时长 |
| `smartcs.creditcard` | `luhn-check` | false | 是否对 `cardNo` 做 Luhn 校验 |
| `smartcs.security.api-key` | `enabled` | false | 是否启用服务端 API-Key 校验（**默认关 = 零回归**） |
| `smartcs.security.api-key` | `header` | X-MCP-Api-Key | 携带 API-Key 的请求头 |
| `smartcs.security.api-key` | `keys` | （空） | API-Key 白名单 |

所有项均可经环境变量覆盖（如 `SMARTCS_CREDITCARD_LUHN_CHECK=true`）。

## 构建与运行

```bash
# 从仓库根目录（推荐，使用 Makefile 目标）
make mcp-server-build     # mvn clean package -DskipTests
make mcp-server-test      # mvn test
make mcp-server-run       # mvn spring-boot:run，SSE 端点 :8090

# 在本目录内使用 Maven
mvn spring-boot:run                 # 本地直连联调
mvn verify                          # 单测 + 并发/幂等/错误码/鉴权 + JaCoCo 覆盖率门槛(≥70%) + 打包
```

启动后：

- MCP SSE 建连端点：`http://127.0.0.1:8090/sse`
- MCP 消息端点：`http://127.0.0.1:8090/mcp/message`（WebMVC starter 默认）
- 健康检查：`http://127.0.0.1:8090/actuator/health`
- Prometheus 指标：`http://127.0.0.1:8090/actuator/prometheus`（含 `mcp_tool_calls_total`、`mcp_tool_call_duration`）

## Docker / 部署

多阶段 `Dockerfile`（`maven:3.9-eclipse-temurin-21` 构建 → `eclipse-temurin:21-jre` 运行）：非 root 用户、
容器化 JVM 内存自适配、`actuator/health` 健康探针、prod profile 默认启用（优雅停机 + 暴露面收敛）。

```bash
# 单独构建镜像
docker build -t smartcs-mcp-server:1.0.0 mcp-server/

# 随网关一并启动（gateway profile，默认 make up 不含）
make gateway-up                     # 启动 nacos + higress + mcp-server
```

`deploy/docker-compose.yml` 的 `mcp-server` 服务归入 `gateway` profile，以 `prod,nacos` profile 启动、
注册到 Nacos，由 Higress 经服务发现路由（SSE ↔ streamable-http 桥接）。CI（`.github/workflows/ci.yml`）
含独立 `mcp-server` job：`setup-java 21` + `mvn -B verify`，与 Python job 并行互不影响。

## 传输与网关

Spring AI 1.0 的 WebMVC MCP starter 采用 **SSE** 传输；而 SmartCS 的 Python `MCPToolClient`
使用 **streamable-http**。生产部署中由 **Higress AI 网关**在前置层完成鉴权、限流与传输桥接，
Python 编排层统一连接网关，无需与本服务的传输方式一一对应。若需本地直连联调，可让 Python 客户端使用 SSE
指向 `http://127.0.0.1:8090/sse`。
