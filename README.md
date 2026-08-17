# qmt-execution-core

面向 Python 自动化交易策略的**可靠执行层（Reliable Execution Layer）**，提供 broker-neutral 的订单执行内核，以及面向 MiniQMT / XtQuant 的生产形态运行时适配。

当前版本：**0.4.1**  
Python：**>= 3.9**

> Core 不决定“买什么、什么时候买、买多少”；Core 负责保证策略已经作出的交易决定能够以**持久化、幂等、可恢复、失败关闭（fail-closed）**的方式安全执行。

## 文档入口

当前只保留三份权威主文档：

1. **[USER_GUIDE.md](docs/USER_GUIDE.md)** — 首次安装、安全连接、策略 / AI Agent 接入。
2. **[SPECIFICATION.md](docs/SPECIFICATION.md)** — 当前产品合同、状态机、安全不变量、0.4.1 Runtime Authority。
3. **[OPERATIONS.md](docs/OPERATIONS.md)** — 生产运行、恢复、Runtime Authority、实盘 gate 与故障处置。

历史冻结规格、审计、实现任务与证据已移入 [`docs/archive/`](docs/archive/)；除非做历史审计，不应把 archive 文档作为当前实现入口。

如果你第一次使用 Core，**只从 `USER_GUIDE.md` 开始**。它已经包含一条不下单的模拟账户初始化路径，并明确告诉你什么时候算首次接入成功。

## Core 解决什么问题

MiniQMT 提供券商交易 API；`qmt-execution-core` 在其上增加可靠执行协议：

```text
Strategy
   |
   | ExecutionRequest
   v
qmt-execution-core
   |
   | durable intent / idempotency / recovery
   | per-symbol claim / shared cash reservation
   | Runtime Authority / fail-closed runtime
   v
MiniQMT / XtQuant
```

典型风险包括：

- submit 已到券商但本地未收到返回，导致重复下单；
- crash / restart 后无法判断上一笔订单是否仍存在；
- cancel 返回成功但订单在撤单过程中成交；
- broker query 返回 `None` / exception 被误判为“没有订单”；
- 多策略进程同时消费同一账户现金；
- 同账户同标的出现两个未闭环 execution；
- 多个 MiniQMT runtime 发生 session-id 冲突；
- shared runtime 被错误配置到不同 coordination DB，形成 split-brain；
- 模拟盘与实盘账户或运行环境绑定错误。

## Core 不负责什么

Core 是执行基础设施，不是策略框架或中央 OMS。它不负责：

- 选股、择时、信号；
- 仓位目标；
- 网格、T-Lot、CorePosition；
- ETF 配置；
- 国债逆回购时机；
- 策略专属 T+1 / settlement 规则；
- 策略专属风控；
- 中央多策略调度；
- RPC execution gateway；
- 分布式 OMS；
- 高频撮合或 Smart Order Routing。

## 0.4.1 关键运行模型

0.4.1 在 0.4 的跨进程 symbol claim / shared cash reservation 上增加 **Account Runtime Authority**：

```text
authoritative account
    -> stable account_key
    -> unique Runtime Authority
    -> Authority-certified coordination DB
```

production shared runtime 中，策略只选择：

```text
runtime_lock_mode = "shared"
```

策略**不允许自行选择** `coordination_path` 或 `authority_root`。

同账户 shared execution 的关键安全边界：

- 同 `(account_key, symbol)` 最多一个 unresolved execution；
- 不同 symbol 可以并发；
- BUY 使用新鲜 broker available cash + 原子 reservation；
- 只有 `ExecutionFinality.RESOLVED` 才释放 symbol/cash resource；
- UNKNOWN / ambiguous outcome 禁止 blind resend；
- Runtime Authority / DB identity mismatch 必须 fail closed。

## 安装与验证

```bash
python -m pip install -e .
qmt-execution-core verify
```

第一次连接 MiniQMT 时不要只看这里；按 **[USER_GUIDE.md](docs/USER_GUIDE.md)** 从 `xtquant` 检查、只读账户探测、binding、Authority bootstrap 一直走到 `quickstart_connect.py` 的三个 PASS。

## 开发验证

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m compileall -q src tests
qmt-execution-core verify
```

`xtquant` 不是 PyPI dependency，由本地 MiniQMT 环境提供。generic Core 与 CI 不依赖真实 QMT 环境。

## 一句话定义

> **qmt-execution-core 是 MiniQMT 之上的可靠执行协议：保证 durable、idempotent、recoverable、fail-closed，并通过 Runtime Authority 和原子账户资源协调安全支持多个独立策略进程。**
