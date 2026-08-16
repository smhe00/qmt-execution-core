# qmt-execution-core

面向 Python 自动化交易策略的**可靠执行层（Reliable Execution Layer）**，提供 broker-neutral 的订单执行内核，以及面向 MiniQMT / XtQuant 的生产形态运行时适配。

本项目的核心目标不是“帮策略决定买什么”，而是保证：

> **当策略已经决定要交易时，这笔交易能够以可持久化、可恢复、幂等、失败关闭（fail-closed）的方式安全执行。**

---

## 1. 项目定位

`qmt-execution-core` 是策略层和券商 API 之间的一层可靠执行基础设施。

```text
策略层决定：
为什么交易 / 什么时候交易 / 买卖多少 / 使用什么价格

                    |
                    | ExecutionRequest
                    v

qmt-execution-core 决定：
是否允许执行 / 如何持久化意图 / 如何防止重复下单
如何处理 UNKNOWN / 如何撤单 / 如何恢复 / 如何 fail closed

                    |
                    | BrokerPort
                    v

MiniQMT / XtQuant / Broker Adapter
```

Core **不负责**：

- 选股；
- 择时；
- 网格策略；
- T-Lot / CorePosition；
- ETF 配置；
- 国债逆回购时机；
- 组合目标权重；
- 策略专属风险模型；
- Portfolio Manager；
- 中央 OMS；
- 多策略中央调度器。

Core 负责的是**执行可靠性**。

---

# 2. 当前版本与正式规格

## 当前已实现版本

```text
qmt-execution-core 0.3.1
Python >= 3.9
```

当前实现已经具备完整的单执行可靠性主干：

- 显式执行状态机；
- Durable Intent / 持久化交易意图；
- 跨周期幂等；
- Crash-safe Journal；
- UNKNOWN 恢复；
- Restart Recovery；
- Cancel / Fill 竞态处理；
- 严格 Broker Query 语义；
- MiniQMT 账户绑定；
- MiniQMT 状态归一化；
- Callback 隔离；
- EventQueue；
- 实盘双重 Gate；
- 状态机形式化验证；
- `before_broker_submit` / `before_broker_cancel` 生命周期 sidecar hook。

## 已冻结的下一版规格

**v0.4 正式规格已经冻结，但尚不等于当前代码已经全部实现。**

正式规格：

- **[qmt-execution-core v0.4 正式规格](docs/CORE_SPEC_V0_4_RESOURCE_COORDINATION.md)**

v0.4 的目标是在不破坏现有同步 API 的前提下，增加：

- `(account_key, symbol)` 跨进程执行 Claim；
- 同账户不同标的独立并发；
- Shared Cash Reservation；
- Conservative Cash Requirement Estimation；
- Fresh Broker Cash Verification；
- 多账户隔离；
- Shared MiniQMT Runtime Mode；
- Bounded Session ID Management；
- Execution Finality：`OPEN / RESOLVED / QUARANTINED`。

### 当前实现与 v0.4 目标对照

| 能力 | 0.3.1 当前状态 | v0.4 目标 |
|---|---|---|
| Durable Intent | 已实现 | 保持 |
| Idempotent Execution | 已实现 | 保持 |
| UNKNOWN Recovery | 已实现 | 保持 |
| Crash / Restart Recovery | 已实现 | 保持 |
| Cancel / Fill Race | 已实现 | 保持 |
| Strict Broker Query | 已实现 | 保持 |
| Explicit State Machine | 已实现 | 保持 |
| MiniQMT Account Binding | 已实现 | 保持 |
| Callback Isolation | 已实现 | 保持 |
| Formal Verification | 已实现 | 扩展验证范围 |
| 单个 `ExecutionSession` 一次一个 active execution | 已实现 | **保持** |
| 同 QMT 路径多进程并行 Runtime | 当前被 qmt-path mutex 阻止 | 新增 shared mode |
| 同账户不同标的并发 | 尚未完整支持 | 必须支持 |
| 同账户同标的跨进程互斥 | 尚未实现 | 新增 `(account_key, symbol)` claim |
| 共享资金原子预留 | 尚未实现 | 新增 |
| 保守交易资金估算 | 尚未实现 | 新增 |
| 多账户资源协调 | 尚未实现 | 新增 |
| Session ID bounded allocator | 尚未实现 | 新增 |

---

# 3. Core 最重要的价值

对于个人或少量低频策略，Core 的价值并不是提高下单速度，而是处理那些“平时不发生，一旦发生就很危险”的执行异常。

## 3.1 防止重复下单

典型异常：

```text
策略调用 submit
    |
    v
Broker 实际已经收到订单
    |
    v
网络/进程异常
    |
    v
策略没有拿到明确返回
```

错误做法：

```text
“刚才可能没下成功，再发一次”
```

Core 的规则：

```text
UNKNOWN
  |
  v
Broker Query / Reconciliation
  |
  +--> 找到原订单 -> 恢复
  |
  +--> 仍然不确定 -> fail closed
```

**UNKNOWN 永远不是重新 submit 的许可。**

---

## 3.2 崩溃与重启恢复

Core 在 broker side effect 之前持久化 execution intent。

进程重启后不会简单“清空状态重新开始”，而是：

```text
Journal
  + Durable Identity
  + Broker Query
        |
        v
恢复已有 execution
```

这样可以避免：

- crash 后重复下单；
- 已成交订单被当成未提交；
- 已撤单订单被错误恢复成 active；
- unresolved order 被丢失。

---

## 3.3 正确处理撤单语义

MiniQMT / 券商 API 返回“撤单请求成功”并不代表订单已经最终取消。

Core 的语义是：

```text
WORKING
   |
   | cancel request
   v
PENDING_CANCEL / CANCELLING
   |
   | authoritative query
   +--> CANCELLED
   +--> PARTIALLY_FILLED
   +--> WORKING
   +--> FILLED
   +--> UNKNOWN
```

特别是：

> **撤单过程中如果 broker 权威状态显示已经成交，最终状态必须是 `FILLED`。**

---

# 4. 当前 0.3.1 架构

```text
TGrid / Reverse Repo / ETF / Rebalance / 其他策略
                         |
                         | ExecutionRequest
                         v
+--------------------------------------------------+
| Generic Execution Core                           |
|                                                  |
| Explicit State Machine                           |
| Durable Intent                                   |
| Cross-cycle Idempotency                          |
| Crash-safe Journal                               |
| Execution Mutex                                  |
| Query-based Recovery / Reconciliation            |
| Formal Explicit-state Verifier                   |
+--------------------------+-----------------------+
                           |
                           | BrokerPort
                           v
+--------------------------------------------------+
| MiniQMT Runtime                                  |
|                                                  |
| Fingerprint-bound Account Selection              |
| XtQuant Status Normalization                     |
| Callback -> Bounded Serial EventQueue            |
| Account Health / Disconnect Recovery             |
| Live Gate + Runtime Confirmation                 |
+--------------------------+-----------------------+
                           |
                           v
                        MiniQMT
```

### 当前并发限制

0.3.1 为了 fail-closed，采用了较保守的 qmt-path-wide runtime mutex。

因此当前版本**不应被理解为已经支持**：

```text
同一个 QMT userdata path
+ 多个独立 writable runtime
+ 同时执行不同标的
```

这正是 v0.4 要解决的主要结构性问题。

---

# 5. v0.4 目标并发模型

v0.4 不把一个 `ExecutionSession` 改造成复杂的多订单引擎。

仍然保持：

> **一个 ExecutionSession 一次只拥有一个 active execution。**

并发来自多个独立策略进程 / Runtime：

```text
        Strategy Process A                     Strategy Process B
        ------------------                     ------------------
        TGrid / Strategy A                     ETF / Strategy B
               |                                      |
               | sync submit / poll / cancel          | sync submit / poll / cancel
               v                                      v
        +-------------------+                  +-------------------+
        | ExecutionSession A|                  | ExecutionSession B|
        | journal / state   |                  | journal / state   |
        | symbol=0700.HK    |                  | symbol=510300.SH  |
        +---------+---------+                  +---------+---------+
                  |                                      |
                  | claim(Account,0700.HK)                | claim(Account,510300.SH)
                  |                                      |
                  +------------------+-------------------+
                                     |
                                     v
                     +----------------------------------+
                     | Shared Account Coordination      |
                     |                                  |
                     | - shared cash reservation        |
                     | - per-symbol execution claim     |
                     | - account_key isolation          |
                     +----------------+-----------------+
                                      |
                                      v
                         +--------------------------+
                         | MiniQMT / QMT Account    |
                         | same account             |
                         | shared cash pool         |
                         +--------------------------+
```

### v0.4 的两个核心并发规则

#### 不同标的允许并发

```text
Account A / 0700.HK   -> WORKING
Account A / 510300.SH -> CANCELLING
```

这是合法状态。

#### 同账户同标的必须串行

如果：

```text
Account A / 0700.HK -> WORKING
```

另一个进程不能同时创建：

```text
Account A / 0700.HK -> NEW EXECUTION
```

必须等原 execution 权威闭环后，才可以释放 `(account_key, symbol)` claim。

---

# 6. v0.4 共享资金模型

多个策略进程可以同时操作不同标的，但如果属于同一个账户，它们共享同一份资金池。

因此 BUY 前需要跨进程原子预留。

## 6.1 保守资金需求

不能只按：

```text
qty * limit_price
```

预留资金。

目标模型：

```text
required_cash
=
max_order_notional
+ conservative_transaction_cost
+ broker_temporary_withholding_buffer
+ optional_fx_or_rounding_buffer
```

例如港股通场景，可以通过 estimator 为：

- 佣金；
- 税费；
- 最低收费；
- 券商临时较高扣款；
- FX buffer；
- rounding buffer；

留出足够安全空间。

Core **不硬编码具体市场费率**。

目标接口类似：

```python
class CashRequirementEstimator(Protocol):
    def estimate(self, request, account_snapshot):
        ...
```

---

## 6.2 新鲜 Broker Cash

每次新 BUY reservation 都必须基于最新券商资金：

```text
effective_available_cash
=
fresh_broker_available_cash
- sum(other_active_reservations)
```

订单权威终态后，可以释放本地 reservation。

但：

> **释放 reservation 不等于把这笔金额直接“加回”本地可用资金。**

下一笔 BUY 必须重新读取 broker available cash。

这样 broker 自己的：

- 实际成交扣款；
- 实际费用；
- 暂时冻结；
- 临时多扣；
- 后续返还；

会自然反映到下一次 authoritative cash snapshot 中。

---

# 7. 多账户模型

v0.4 从数据模型上支持 N 个账户。

资源隔离基本键：

```text
account_key
```

标的执行互斥键：

```text
(account_key, symbol)
```

因此：

```text
Account A / 0700.HK
Account B / 0700.HK
```

属于两个独立 execution claim，可以并行。

推荐：

```text
qec_coord_live_A.db
qec_coord_live_B.db
qec_coord_sim_A.db
```

也允许未来统一到：

```text
qec_coord.db
```

但 schema 始终应携带 `account_key`。

---

# 8. 核心状态机

当前核心状态包括：

```text
IDLE
WAIT_TRIGGER
TRIGGER
PRE_CHECK
SUBMITTED
ACCEPTED
WORKING
UNKNOWN
PARTIALLY_FILLED
PENDING_CANCEL
CANCELLING
CANCEL_REJECTED
FILLED
CANCELLED
REJECTED
FAILED
```

核心语义：

- `UNKNOWN`：可恢复，不能盲目重发；
- `CANCEL_REJECTED`：可恢复，不是 terminal；
- `FILLED`：broker 权威成交；
- `CANCELLED`：broker 权威取消；
- `REJECTED`：明确拒单；
- `FAILED`：本地执行失败，但是否允许释放同标的资源不能只看状态名。

v0.4 正式规格进一步定义：

```text
ExecutionFinality

OPEN
RESOLVED
QUARANTINED
```

例如：

```text
WORKING / UNKNOWN / CANCEL_REJECTED -> OPEN
FILLED / CANCELLED                  -> RESOLVED
FAILED + broker reality unresolved -> QUARANTINED
```

---

# 9. 核心安全不变量

当前及 v0.4 必须保持的原则：

1. **Durable Before Side Effect**  
   Broker submit 之前必须存在可恢复的 durable intent。

2. **No Blind Resend**  
   UNKNOWN / timeout / exception 不能直接触发重新 submit。

3. **Strict Query Semantics**  
   `None`、异常、未知状态不能静默解释成“没有订单”。

4. **Cancel Request != Cancelled**  
   撤单请求成功不是终态取消。

5. **Fill Wins During Cancel Race**  
   撤单过程中成交，最终必须归一为 `FILLED`。

6. **Restart Means Recover, Not Reset**  
   重启必须恢复已有 execution，而不是从零开始。

7. **Callback Does Not Own Execution Authority**  
   callback 只产生 immutable observation，不直接 submit / cancel / journal mutation。

8. **Live Must Fail Closed**  
   实盘能力默认关闭，不能因 transport 重连自动恢复下单权限。

9. **Same Account + Same Symbol Is Serialized**（v0.4）  
   同账户同标的未闭环时不得开启第二个 execution。

10. **Different Symbols Must Not Block Each Other**（v0.4）  
    不同标的不能因全局 execution lock 被无意义串行化。

11. **Shared Cash Reservation Before BUY Submit**（v0.4）  
    多 writer 共享账户资金时，BUY 必须先完成跨进程原子资金预留。

---

# 10. BrokerPort

Core 通过 broker-neutral `BrokerPort` 隔离具体券商 API。

当前核心接口保持窄而稳定：

```python
place_order(request) -> order_id
cancel_order(order_id) -> cancel_result
query_order(order_id) -> BrokerOrder
query_orders() -> tuple[BrokerOrder, ...]
execution_healthy() -> bool
```

v0.4 如需要账户资源查询，优先通过**新增接口**而不是破坏现有 `BrokerPort`：

```python
class AccountResourcePort(Protocol):
    def query_asset(self) -> BrokerAsset:
        ...
```

这样普通 FakeBroker / SimBroker 不需要被迫实现账户资产 API。

---

# 11. MiniQMT 状态归一化

MiniQMT / XtQuant 原始订单状态会被归一到 Core 的 broker-neutral 状态。

| QMT 状态 | 值 | Core 归一状态 |
|---|---:|---|
| `UNREPORTED` | 48 | `ACCEPTED` |
| `WAIT_REPORTING` | 49 | `ACCEPTED` |
| `REPORTED` | 50 | `WORKING` |
| `REPORTED_CANCEL` | 51 | `CANCEL_PENDING` |
| `PARTSUCC_CANCEL` | 52 | `CANCEL_PENDING` + partial fill |
| `PART_CANCEL` | 53 | `PARTIAL_CANCELLED` |
| `CANCELED` | 54 | `CANCELLED` |
| `PART_SUCC` | 55 | `PARTIALLY_FILLED` |
| `SUCCEEDED` | 56 | `FILLED` |
| `JUNK` | 57 | `REJECTED` |
| `UNKNOWN` | 255 | `UNKNOWN` |
| 未识别值 | other | `UNKNOWN` |

未知 raw status 永远不会被默认当成成功或空订单。

---

# 12. 安装

开发安装：

```bash
python -m pip install -e .
```

安装开发依赖：

```bash
python -m pip install -e ".[dev]"
```

当前支持：

```text
Python >= 3.9
```

`xtquant` **不是 PyPI dependency**。

它由本地 MiniQMT / QMT 环境提供，因此：

- generic core 可以在没有 MiniQMT 的 CI 中测试；
- MiniQMT adapter 在实际运行环境中通过 dependency injection / lazy import 接入。

---

# 13. 通用 Broker 使用示例

任何实现 `BrokerPort` 的 broker 都可以接入 `ExecutionSession`。

```python
from qmt_execution_core import ExecutionSession

session = ExecutionSession(
    broker=my_broker,
    guard=my_project_guard,
    journal_path="runtime/order.json",
    lock_path="runtime/order.lock",
    execution_id="strategy-a",
)

session.open()

try:
    snapshot = session.submit(request)
    snapshot = session.poll()
finally:
    session.close()
```

`ExecutionSession` 当前和 v0.4 都保持：

> **one active execution at a time**

它不是一个中央多订单 OMS。

---

# 14. MiniQMT 使用示例

## 14.1 创建本地账户绑定

```bash
qmt-execution-core create-binding \
  --environment simulation \
  --account-type 2 \
  --qmt-path "C:/.../userdata_mini" \
  --output config/account-binding.local.json
```

Binding 使用 fingerprint / hash 绑定目标账户和 QMT 路径，不要求 Core 在配置中长期保存明文账户 ID。

---

## 14.2 创建 Runtime

```python
from qmt_execution_core.miniqmt import (
    MiniQmtRuntime,
    MiniQmtRuntimeConfig,
)

config = MiniQmtRuntimeConfig.from_json("config/runtime.local.json")

runtime = MiniQmtRuntime.connect(
    config,
    guard=my_project_guard,
)

try:
    snapshot = runtime.submit(request)
    snapshot = runtime.poll()
finally:
    runtime.close()
```

---

# 15. 实盘安全边界

Core 的实盘执行默认 fail-closed。

仅仅配置：

```text
live_trading_enabled = true
```

仍然不足以获得实盘下单权限。

Runtime 还需要运行时确认：

```python
runtime.confirm_live("runtime-only-token")
```

核心原则：

```text
配置允许 live
        +
运行时确认 token
        +
账户绑定正确
        +
账户状态健康
        +
ExecutionGuard 当前证据通过
        +
EventQueue 健康
        =
允许新的 broker side effect
```

以下事件会使新订单能力失效：

- disconnect；
- runtime teardown；
- account health failure；
- EventQueue unhealthy；
- guard fail；
- recovery 尚未完成。

Transport reconnect 本身**不会**自动重新赋予下单能力。

---

# 16. Callback 隔离

MiniQMT callback thread 不拥有策略执行权限。

推荐结构：

```text
XtQuant Callback
      |
      | immutable observation
      v
Bounded Serial EventQueue
      |
      v
Execution Processing
```

Callback 不直接：

- submit；
- cancel；
- 修改 journal；
- 修改 reservation；
- 修改策略状态。

EventQueue unhealthy 时，新下单必须 fail closed。

---

# 17. 项目专属职责边界

## Core Owns

```text
Execution lifecycle
Durable intent
Idempotency
UNKNOWN recovery
Crash/restart recovery
Cancel/fill race
Strict broker query
BrokerPort abstraction
MiniQMT account binding
Callback isolation
Live safety gate
Formal verification
```

v0.4 进一步增加：

```text
(account_key, symbol) execution claim
shared cash coordination
execution finality
shared MiniQMT runtime mode
session-id coordination
```

## Strategy / Project Owns

```text
why to trade
when to trade
how much to trade
signal generation
position sizing
VWAP / anchor
T+1 / settlement business rules
strategy-specific risk policy
symbol-to-strategy assignment
```

Core 不负责决定“某个标的应该属于哪个策略”。

Core 只负责保证：

> **同一账户、同一标的，在上一笔 execution 尚未安全闭环时，不允许开启重叠的第二笔 execution。**

---

# 18. 当前 Package Layout

```text
src/qmt_execution_core/
├── domain.py
├── state_machine.py
├── session.py
├── journal.py
├── mutex.py
├── recovery.py
├── guards.py
├── event_queue.py
├── verifier.py
├── ports.py
└── miniqmt/
    ├── status.py
    ├── adapter.py
    ├── callbacks.py
    ├── binding.py
    ├── runtime_gate.py
    └── runtime.py
```

v0.4 的 coordination 模块会在正式实现后再更新此目录，不在 README 中预先伪造尚不存在的文件结构。

---

# 19. 验证与测试

修改执行语义前至少运行：

```bash
python -m pytest
python -m compileall -q src tests
PYTHONPATH=src python -c "from qmt_execution_core import verify_state_machine; print(verify_state_machine())"
```

也可以使用 CLI：

```bash
qmt-execution-core verify
```

当前验证体系包括：

- unit tests；
- state-machine refinement tests；
- explicit-state verifier；
- protected source hash binding；
- journal/spec binding；
- Windows mutex probes；
- wheel build / clean install；
- installed-wheel verifier；
- MiniQMT read-only / fake runtime tests。

形式化 verifier 当前检查：

- 没有不可达状态；
- 没有不可达 transition；
- 没有 reachable invariant violation；
- 每个 reachable non-terminal state 都存在 terminal path；
- `UNKNOWN` 不存在 blind retry / resend path。

> Formal verification 不替代 runtime refinement test。

---

# 20. v0.4 最低验收场景

冻结规格要求至少覆盖：

1. ambiguous submit -> restart -> 找回原 broker order -> submit count 仍为 1；
2. `SUBMITTED -> UNKNOWN -> WORKING -> FILLED`；
3. `WORKING -> cancel -> CANCELLING -> FILLED`；
4. `CANCEL_REJECTED -> UNKNOWN -> WORKING -> FILLED`；
5. active / cancel / unknown 状态重启恢复；
6. 同账户同标的两个进程竞争 -> 第二个 broker submit 前被阻止；
7. 同账户不同标的两个进程 -> 可以同时 active；
8. 共享资金 100k，同时 reserve 60k + 50k -> 不允许超额；
9. conservative estimator 可以使 reservation 大于订单名义金额；
10. execution terminal 后释放 reservation，但下一笔必须 refresh broker cash；
11. query `None` / exception / unknown raw status 不产生 resend 权限；
12. shared mode 同一 qmt path 两个 runtime 获得不同 session ID；
13. session ID 冲突只允许有限 fallback，不允许无限随机重试；
14. 0.3.1 现有 public API 保持 source-compatible。

---

# 21. v0.4 明确不做什么

v0.4 不引入：

- `order_stock_async()` 执行路径；
- 中央策略 Scheduler；
- RPC / Gateway Service；
- 跨机器分布式协调；
- 一个 `ExecutionSession` 内同时管理多个 active order；
- 高频交易撮合级吞吐；
- Smart Order Routing；
- Strategy Framework；
- Portfolio Manager；
- TGrid / ETF / Reverse Repo 等项目业务逻辑。

目标原则是：

> **Process-level concurrency + synchronous broker side effects**  
> **进程级并发 + 单笔同步执行。**

---

# 22. 文档

正式规格和设计文档：

- **[v0.4 冻结正式规格](docs/CORE_SPEC_V0_4_RESOURCE_COORDINATION.md)**
- [架构说明](docs/ARCHITECTURE.md)
- [状态机规格](docs/STATE_MACHINE_SPEC.md)
- [MiniQMT Profile](docs/MINIQMT_PROFILE.md)
- [Production MiniQMT Runtime](docs/PRODUCTION_RUNTIME.md)
- [CHANGELOG](CHANGELOG.md)

其中：

> **`docs/CORE_SPEC_V0_4_RESOURCE_COORDINATION.md` 是 v0.4 实现的 canonical frozen specification。**

如需修改冻结规格，应显式升级规格版本，而不是在实现过程中静默改变关键不变量。

---

# 23. 开发原则

本仓库是 reusable execution infrastructure，不是策略项目。

提交代码时必须保持以下边界：

- 不把 TGrid / Grid / ETF / Reverse Repo 等业务规则写入 Core；
- generic core 不直接 import `xtquant`；
- MiniQMT 逻辑只放在 `miniqmt/` profile；
- UNKNOWN 只能通过 broker query / reconciliation 恢复；
- durable intent 必须先于 broker submit；
- durable cancel intent 必须先于 broker cancel；
- callback 不拥有执行 side effect 权限；
- live safety gate 不得弱化；
- 状态机语义变化必须同步修改 verifier 和 refinement test。

详见 [AGENTS.md](AGENTS.md)。

---

# 24. License

MIT License。
