# qmt-execution-core

面向 Python 自动化交易策略的**可靠执行层（Reliable Execution Layer）**，提供 broker-neutral 的订单执行内核，以及面向 MiniQMT / XtQuant 的生产形态运行时适配。

当前版本：**0.4.0**  
Python：**>= 3.9**

> Core 不决定“买什么、什么时候买、买多少”；Core 负责保证策略已经作出的交易决定能够以**持久化、幂等、可恢复、失败关闭（fail-closed）**的方式安全执行。

正式规格：**[qmt-execution-core v0.4 冻结规格](docs/CORE_SPEC_V0_4_RESOURCE_COORDINATION.md)**

---

## 1. Core 解决什么问题

MiniQMT 提供的是券商交易 API；`qmt-execution-core` 在其上增加可靠执行协议。

```text
Strategy / TGrid / ETF / Repo / Future Strategy
                     |
                     | ExecutionRequest
                     v
+----------------------------------------------------+
| qmt-execution-core                                 |
|                                                    |
| Durable Intent                                     |
| Idempotency                                        |
| Explicit State Machine                             |
| UNKNOWN / Restart Recovery                         |
| Cancel / Fill Race Handling                        |
| Per-Symbol Execution Claim                        |
| Shared Cash Reservation                            |
| Execution Finality                                 |
| Fail-Closed Runtime Safety                         |
+----------------------------+-----------------------+
                             |
                             | BrokerPort
                             v
+----------------------------------------------------+
| MiniQMT Runtime / XtQuant Adapter                  |
|                                                    |
| Account Binding                                    |
| Status Normalization                               |
| Callback Isolation                                 |
| Live Gate                                          |
| Shared/Exclusive Runtime Mode                      |
| Bounded Session-ID Lease                           |
+----------------------------+-----------------------+
                             |
                             v
                          MiniQMT
```

典型风险包括：

- submit 已到券商但本地没有收到返回，导致重复下单；
- 进程 crash 后无法判断上一笔订单是否仍存在；
- 撤单请求返回成功，却在撤单过程中实际成交；
- broker query 返回 `None` / exception 被误判为“没有订单”；
- 两个策略进程同时使用同一份账户现金；
- 两个进程同时对同账户同标的开启新的 execution；
- 多个 MiniQMT runtime 使用冲突 session id；
- 模拟盘与实盘环境或账户绑定错误。

这些属于 Core 的职责。

---

## 2. Core 不负责什么

Core 是执行基础设施，不是策略框架或中央 OMS。

Core **不负责**：

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

一个策略通常只需要理解：

```text
ExecutionRequest
submit()
poll()
cancel()
reconcile()
ExecutionSnapshot
```

---

## 3. 0.4.0 主要能力

### 3.1 可靠订单生命周期

- Durable Intent：broker submit 前先持久化交易意图；
- Durable Cancel Intent：broker cancel 前先持久化撤单意图；
- client order id / order remark 跨周期不可重复使用；
- crash-safe journal；
- UNKNOWN 是一等可恢复状态；
- UNKNOWN 永远不是 blind resend 的许可；
- restart 后通过 broker query / reconciliation 恢复；
- cancel API 成功只代表“撤单请求已发送”，不代表最终 CANCELLED；
- fill-during-cancel 最终必须收敛为 `FILLED`；
- query `None`、异常、未知状态均 fail closed；
- broker callback 只进入 bounded serial EventQueue，不直接拥有执行权。

### 3.2 跨进程同标的串行化

0.4.0 增加 durable `(account_key, symbol)` execution claim：

> **同一账户、同一标的，在任意时刻最多只能存在一个未闭环 execution。**

例如：

```text
Account A / 0700.HK -> WORKING
```

此时另一个进程尝试：

```text
Account A / 0700.HK -> NEW EXECUTION
```

会在 broker side effect 之前 fail closed。

### 3.3 不同标的独立并发

不同标的不因为另一笔 execution 正在活动而被全局阻塞：

```text
Process A / Account A / 0700.HK   -> WORKING
Process B / Account A / 510300.SH -> CANCELLING
```

这是 0.4.0 明确支持的合法状态。

`ExecutionSession` 本身仍保持：

> **一次只拥有一个 active execution。**

并发来自多个独立策略进程 / runtime / session，而不是把单个 session 改成多订单引擎。

---

## 4. 共享账户协调模型

推荐结构：

```text
        Strategy Process A                     Strategy Process B
        ------------------                     ------------------
               |                                      |
               v                                      v
        +-------------------+                  +-------------------+
        | ExecutionSession A|                  | ExecutionSession B|
        | journal / state   |                  | journal / state   |
        | symbol=0700.HK    |                  | symbol=510300.SH  |
        +---------+---------+                  +---------+---------+
                  |                                      |
                  +------------------+-------------------+
                                     |
                                     v
                     +----------------------------------+
                     | SQLite Shared Coordination       |
                     |                                  |
                     | (account_key,symbol) claim       |
                     | shared BUY cash reservation      |
                     +----------------+-----------------+
                                      |
                                      v
                         +--------------------------+
                         | MiniQMT / QMT Account    |
                         | shared cash pool         |
                         +--------------------------+
```

共享 SQLite 只保存**真正需要跨进程原子一致性**的资源，不承担策略业务数据库职责。

策略自己的：

- signals；
- position model；
- TGrid business ledger；
- ETF state；
- strategy journal；

仍由各项目自己管理。

---

## 5. Shared Cash Reservation

多个 writer 使用同一账户时，BUY 必须在 broker submit 前完成原子资金预留。

### 5.1 保守资金需求

不能只使用：

```text
qty * limit_price
```

Core 提供 broker-neutral estimator 接口：

```python
CashRequirementEstimator
CashRequirementEstimate
ConservativeCashRequirementEstimator
```

保守需求可以包含：

```text
required_cash
=
max_order_notional
+ conservative_transaction_cost
+ temporary_withholding_buffer
+ fx_rounding_buffer
+ safety_buffer
```

Core **不硬编码港股通、A 股、税费、佣金或 FX 规则**；市场/账户适配层负责配置 estimator。

示例：

```python
from qmt_execution_core import ConservativeCashRequirementEstimator

estimator = ConservativeCashRequirementEstimator(
    fee_rate=0.001,
    minimum_fee=5.0,
    temporary_withholding_buffer=100.0,
    fx_rounding_rate=0.002,
    safety_buffer=50.0,
)
```

对存在临时较高扣款的交易通道，可以通过 `temporary_withholding_buffer` 做保守覆盖。

### 5.2 新鲜券商资金

每笔新 BUY reservation 都必须基于新鲜的权威 broker cash：

```text
effective_available_cash
=
fresh_broker_available_cash
- sum(other_active_reservations_for_same_account)
```

SQLite 使用原子写事务完成 read-check-reserve，防止两个进程同时消费同一份 cash snapshot。

### 5.3 没有 Settlement Pending 本地账本

0.4.0 **不维护额外的 settlement-pending cash ledger**。

当 execution 权威闭环后，本地 reservation 可以释放；但释放 reservation：

> **绝不意味着把该金额直接加回“本地可用现金”。**

下一笔 BUY 必须重新 query broker available cash。

因此券商自己的：

- 实际成交扣款；
- 最终费用；
- 临时冻结 / 多扣；
- 后续返还；

自然反映在下一次权威 cash snapshot 中。

---

## 6. Execution Finality

0.4.0 在原状态机之上增加：

```python
ExecutionFinality.OPEN
ExecutionFinality.RESOLVED
ExecutionFinality.QUARANTINED
```

它用于回答：

> **这个 execution 是否已经安全到可以释放 `(account_key, symbol)`？**

典型映射：

```text
WORKING / PARTIALLY_FILLED / UNKNOWN / CANCEL_REJECTED
    -> OPEN

FILLED / CANCELLED / definitive REJECTED
    -> RESOLVED

FAILED + broker reality unresolved
    -> QUARANTINED

FAILED + broker side effect proven never invoked
    -> RESOLVED
```

因此不能简单写：

```python
if state == FAILED:
    release_symbol()
```

### PRE_BROKER_ABORTED

0.4.0 新增状态机事件 `PRE_BROKER_ABORTED`。

当同步 pre-broker hook 失败时，代码可以机械证明：

```text
BrokerPort.place_order() call count == 0
```

此时状态仍可表现为 `FAILED`，但：

```text
unresolved_order = False
ExecutionFinality = RESOLVED
```

与真正的 broker ambiguity 严格区分。

---

## 7. Submit 顺序

协调模式下固定为：

```text
ExecutionRequest
      |
      v
Guard / PRE_CHECK
      |
      v
Core Durable Intent COMMIT
      |
      v
Shared Coordination
  - claim(account_key, symbol)
  - fresh broker cash
  - conservative BUY reservation
      |
      v
Project before_broker_submit sidecar
      |
      v
BrokerPort.place_order()
```

因此：

- 同标的冲突在项目 sidecar 之前被拒绝；
- 共享资金不足在项目 sidecar 之前被拒绝；
- project sidecar 运行时 shared claim/reservation 已经 durable；
- project sidecar 失败时 broker 尚未调用，shared resources 会释放并记录 proven-no-submit finality；
- broker side effect 仍只有一个 authority。

现有 `before_broker_submit` / `before_broker_cancel` API 保留。

---

## 8. 多账户

0.4.0 的资源隔离从第一版就按 N 个账户设计。

资金池粒度：

```text
account_key
```

标的 claim 粒度：

```text
(account_key, symbol)
```

因此：

```text
Account A / 0700.HK
Account B / 0700.HK
```

可以并发。

`account_key` 从已有 account binding 的：

```text
environment
account_type
account_id_sha256
```

派生，不需要在 coordination DB 中保存明文账户 ID。

推荐个人使用时每个账户一个 DB，例如：

```text
qec_coord_live_A.db
qec_coord_live_B.db
qec_coord_sim_A.db
```

但 schema 本身支持一个 DB 存多个 `account_key`。

---

## 9. MiniQMT Runtime：exclusive 与 shared

### 默认：exclusive

为了完全向后兼容，默认仍是：

```python
runtime_lock_mode="exclusive"
```

它保留原 qmt-path-wide runtime mutex。

### 多进程：shared

需要同一个 QMT userdata 下多个策略 runtime 并存时，显式使用：

```python
runtime_lock_mode="shared"
```

shared mode 要求：

- `coordination_path` 或显式注入 `ExecutionCoordinator`；
- 不再获取 qmt-path-wide exclusive runtime mutex；
- 每个 runtime 拥有独立 MiniQMT session id；
- 同标冲突由 durable symbol claim 管理；
- BUY cash race 由 SQLite coordination 管理。

---

## 10. Bounded Session ID

shared mode 不使用无限随机 session id。

`BoundedSessionIdAllocator`：

- 使用有限 session-id pool；
- 支持 caller supplied exact `session_id`；
- 自动模式从稳定 preferred candidate 开始；
- session id 冲突时进行有限 fallback；
- connect failure 只进行有限尝试；
- 每个 session id 使用 OS file lock lease；
- 进程 crash 后由操作系统释放 lease；
- 不假定 MiniQMT 提供 allocate/release session-id API。

---

## 11. 安装与开发

```bash
python -m pip install -e .
```

开发环境：

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m compileall -q src tests
qmt-execution-core verify
```

`xtquant` **不是 PyPI dependency**。它由本地 MiniQMT 环境提供，因此 generic Core 可以在没有 MiniQMT 的 CI 环境中测试。

---

## 12. Generic Broker 使用

任何实现 `BrokerPort` 的 broker 都可以使用普通 `ExecutionSession`：

```python
from qmt_execution_core import ExecutionSession

session = ExecutionSession(
    broker=my_broker,
    guard=my_guard,
    journal_path="runtime/order.json",
    lock_path="runtime/order.lock",
    execution_id="strategy-a",
)

session.open()
```

需要共享账户协调时可以显式使用：

```python
from qmt_execution_core import (
    CoordinatedExecutionSession,
    SQLiteExecutionCoordinator,
)

coordinator = SQLiteExecutionCoordinator("runtime/qec_coord.db")

session = CoordinatedExecutionSession(
    broker=my_broker,
    account_resource=my_broker,
    guard=my_guard,
    journal_path="runtime/order.json",
    lock_path="runtime/order.lock",
    coordinator=coordinator,
    account_key="derived-account-key",
    cash_estimator=my_cash_estimator,
    execution_id="strategy-a",
)
```

---

## 13. MiniQMT shared runtime 示例

```python
from qmt_execution_core import ConservativeCashRequirementEstimator
from qmt_execution_core.miniqmt import MiniQmtRuntime, MiniQmtRuntimeConfig

config = MiniQmtRuntimeConfig(
    environment="simulation",
    qmt_path="C:/.../userdata_mini",
    binding_path="config/account-binding.local.json",
    journal_path="runtime/tgrid-journal.json",
    lock_path="runtime/tgrid-exec.lock",
    strategy_name="TGRID",
    runtime_lock_mode="shared",
    coordination_path="runtime/qec_coord_sim.db",
)

estimator = ConservativeCashRequirementEstimator(
    fee_rate=0.001,
    safety_buffer=100.0,
)

runtime = MiniQmtRuntime.connect(
    config,
    guard=my_guard,
    cash_estimator=estimator,
)

try:
    snapshot = runtime.submit(request)
finally:
    runtime.close()
```

实盘依然需要双重 gate；仅仅配置：

```text
live_trading_enabled=true
```

仍不足以启用真实订单。

---

## 14. MiniQMT 状态归一化

| QMT | Value | Core |
|---|---:|---|
| UNREPORTED | 48 | ACCEPTED |
| WAIT_REPORTING | 49 | ACCEPTED |
| REPORTED | 50 | WORKING |
| REPORTED_CANCEL | 51 | CANCEL_PENDING |
| PARTSUCC_CANCEL | 52 | CANCEL_PENDING + partial fill |
| PART_CANCEL | 53 | PARTIAL_CANCELLED |
| CANCELED | 54 | CANCELLED |
| PART_SUCC | 55 | PARTIALLY_FILLED |
| SUCCEEDED | 56 | FILLED |
| JUNK | 57 | REJECTED |
| UNKNOWN | 255 | UNKNOWN |
| 其他未知值 | other | UNKNOWN |

未知 broker 状态不会静默解释成“订单不存在”。

---

## 15. Formal Verification

Core 的 explicit-state verifier 检查：

- 所有声明 state / transition 可达；
- 每个 reachable non-terminal state 都存在 terminal path；
- 没有 reachable invariant violation；
- UNKNOWN 不存在 blind retry/new-order edge；
- v0.4 finality refinement：
  - UNKNOWN / CANCEL_REJECTED 必须 OPEN；
  - unresolved FAILED 必须 QUARANTINED；
  - FILLED / CANCELLED / REJECTED 必须 RESOLVED；
- 所有 protected execution source 都存在并参与 SHA-256 binding。

运行：

```bash
qmt-execution-core verify
```

Formal verification 不替代 runtime / fault-injection / cross-process tests。

---

## 16. CI / 验证范围

当前 CI 包含：

- Linux Python 3.9 / 3.11 / 3.12 full pytest；
- compileall；
- wheel build；
- clean wheel reinstall；
- out-of-tree installed verifier；
- Windows 3.11 safety probes；
- Windows msvcrt execution mutex；
- SQLite cross-process same-symbol race；
- SQLite shared-cash race；
- shared MiniQMT runtime coexistence；
- session-id lease conflict / release；
- bounded connection fallback；
- UNKNOWN -> FAILED/QUARANTINED claim retention；
- project sidecar ordering与 proven-no-submit。

这些测试全部使用 fake broker / fake XtQuant；CI **不发送真实或模拟 QMT 订单**。

---

## 17. 从 0.3.1 升级到 0.4.0

### Public API

以下主要外部形状保持兼容：

- `ExecutionRequest`；
- `ExecutionSnapshot`；
- `BrokerPort` 原有方法；
- `ExecutionGuard`；
- `ExecutionSession.submit/poll/cancel/reconcile/next_cycle`；
- `MiniQmtRuntime.submit/poll/cancel/next_cycle`；
- `before_broker_submit` / `before_broker_cancel`。

0.4 的 coordination API 是 additive。

### Journal 升级注意事项

Core journal 会绑定：

```text
transition_spec_sha256
execution_source_sha256
```

0.4.0 增加了新的执行源码和状态机 refinement，因此 **0.3.1 journal 的 source/spec hash 与 0.4.0 不相同**。

这是故意的安全边界，不应该通过忽略 hash 绕过。

升级原则：

1. 先用 0.3.1 / broker authoritative query 确认旧 execution 已经权威闭环；
2. 确认不存在 UNKNOWN、WORKING、CANCELLING 等未解决订单；
3. 保留旧 journal 作为审计记录；
4. 为 0.4 runtime 使用新的 journal 路径；
5. 再启用 shared coordination。

> **不要为了通过 hash 校验而直接删除一个仍可能存在 active broker order 的旧 journal。**

---

## 18. 当前 Non-Goals

0.4.0 不提供：

- `order_stock_async()` 执行路径；
- 单个 ExecutionSession 内多订单并发；
- 中央 strategy scheduler；
- RPC/gateway service；
- 跨机器 distributed coordination；
- Shared Position Coordinator；
- 策略专属交易费率硬编码。

当前推荐模型仍然是：

> **进程级并发 + 每个 session 同步执行 + 共享 SQLite 只协调账户级冲突资源。**

---

## 19. 文档

- **[v0.4 冻结正式规格](docs/CORE_SPEC_V0_4_RESOURCE_COORDINATION.md)**
- [Architecture](docs/ARCHITECTURE.md)
- [State Machine Specification](docs/STATE_MACHINE_SPEC.md)
- [MiniQMT Profile](docs/MINIQMT_PROFILE.md)
- [Production Runtime](docs/PRODUCTION_RUNTIME.md)
- [Changelog](CHANGELOG.md)

---

## 20. 一句话定义

> **qmt-execution-core 是 MiniQMT 之上的可靠执行协议：保证 durable、idempotent、recoverable、fail-closed，同时允许同账户不同标的跨进程并发，并通过原子资源协调防止同标 execution 重叠和共享现金超用。**
