# qmt-execution-core

面向 Python 自动化交易策略的**可靠执行层（Reliable Execution Layer）**。它在 MiniQMT / XtQuant 之上提供 broker-neutral 的订单执行内核，以及持久化、幂等、恢复、跨进程协调和 fail-closed 运行时安全。

当前版本：**0.4.1**  
Python：**>= 3.9**

> Core 不决定“买什么、什么时候买、买多少”；Core 负责把策略已经作出的交易决定安全地执行到底。

## 文档入口

主文档只保留三份：

- **[USER_GUIDE.md](docs/USER_GUIDE.md)**：策略/AI 如何安装、接入和调用 Core。
- **[SPECIFICATION.md](docs/SPECIFICATION.md)**：当前 0.4.1 产品边界、状态机、并发与安全不变量。
- **[OPERATIONS.md](docs/OPERATIONS.md)**：MiniQMT 运行、Runtime Authority、恢复、实盘 gate 与故障处理。

另外：

- **[AGENTS.md](AGENTS.md)**：修改 Core 代码时必须遵守的 Agent 合同。
- **[CHANGELOG.md](CHANGELOG.md)**：版本变更。
- **[docs/archive/](docs/archive/)**：旧冻结规格、审计、实现任务和验证证据，仅保留历史，不作为当前权威接口说明。

## Core 解决什么问题

```text
Strategy
   |
   | ExecutionRequest
   v
qmt-execution-core
   |
   | Durable intent / idempotency / recovery
   | per-symbol claim / shared cash reservation
   | Runtime Authority / live safety gate
   v
MiniQMT / XtQuant
```

核心能力：

- durable submit / cancel intent；
- crash-safe journal；
- `UNKNOWN` / restart reconciliation；
- cancel/fill race handling；
- 严格 broker query 语义；
- 同账户同标的 execution 串行化；
- 同账户不同标的跨进程并发；
- shared BUY 原子资金预留；
- 账户绑定与 Runtime Authority；
- callback 隔离、断线失效与恢复；
- 实盘双重 gate；
- explicit state-machine / source integrity verification。

## Core 不负责什么

Core 不是策略框架、Portfolio Manager、中央 OMS 或 RPC execution gateway。以下仍由策略项目负责：

- 选股、择时、信号；
- 仓位目标与组合配置；
- TGrid / Grid / T-Lot / CorePosition；
- ETF、逆回购等业务规则；
- 市场专属 T+1 / settlement；
- 策略风险预算、fresh quote、position/can_use 检查；
- 中央多策略调度。

策略不得绕过 Core 直接调用 XtQuant 下单/撤单接口。

## 最小调用面

普通策略通常只需要：

```python
runtime.submit(request)
runtime.poll()
runtime.cancel()
runtime.next_cycle()
runtime.recover_after_disconnect()
runtime.close()
```

以及：

```text
MiniQmtRuntime
MiniQmtRuntimeConfig
ExecutionRequest
ExecutionSnapshot
Side
TradeState
ExecutionGuard
CashRequirementEstimator
```

完整接入流程见 [docs/USER_GUIDE.md](docs/USER_GUIDE.md)。

## 安全默认值

- 默认使用模拟环境完成接入验证；
- `UNKNOWN` 不允许 blind resend；
- cancel API 成功不等于最终 `CANCELLED`；
- shared runtime 必须通过账户唯一 Runtime Authority；
- 实盘仅在配置 gate 和运行时确认同时满足时才可能开放；
- 任何账户、运行时、broker reality 或 coordination 身份不确定时均 fail closed。

## 开发验证

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m compileall -q src tests
qmt-execution-core verify
```

`xtquant` 由本地 MiniQMT 环境提供，不是 PyPI 依赖。
