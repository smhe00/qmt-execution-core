# qmt-execution-core v0.4 正式规格 / Formal Specification

> **Status / 状态：FROZEN — APPROVED FOR IMPLEMENTATION / 已冻结并批准实现**  
> **Specification Version / 规格版本：v0.4**  
> **Implementation Baseline / 实现基线：qmt-execution-core 0.3.1 @ `937e6a4a1cbd54df960f9bde3ca2e91d6bc19c79`**  
> **Frozen Date / 冻结日期：2026-08-16**  
> **Target Model / 目标模型：个人、少量低频策略；支持 N 个 QMT 账户；多个独立策略进程可并发运行；同账户不同标的允许并发执行；同账户共享资金池。**

---

## 1. 定位 / Positioning

`qmt-execution-core` 是面向 MiniQMT/QMT 自动化交易的**可靠执行层 / Reliable Execution Layer**。

Core 的目标不是提供策略框架、中央调度器、Portfolio Manager、中央 OMS、RPC Gateway 或高频交易引擎，而是让所有策略复用同一套可靠执行协议：

- Durable Intent / 持久化交易意图；
- Idempotent Execution / 幂等执行；
- UNKNOWN Recovery / 未知状态恢复；
- Crash & Restart Recovery / 崩溃与重启恢复；
- Cancel/Fill Race Handling / 撤单成交竞态处理；
- Strict Broker Query Semantics / 严格券商查询；
- Per-Symbol Execution Serialization / 同标的执行串行化；
- Independent Cross-Symbol Concurrency / 不同标的独立并发；
- Safe MiniQMT Account & Runtime Integration / 安全账户与运行环境接入。

外部执行形态保持同步：

```python
request = ExecutionRequest(...)
snapshot = runtime.submit(request)
snapshot = runtime.poll()
snapshot = runtime.cancel()
```

v0.4 不引入 `order_stock_async()` 执行路径。

---

## 2. 目标使用模型 / Target Operating Model

### 2.1 多账户 / Multi-Account

当前实际部署可以只有一个实盘账户和一个模拟账户，但 Core 的正式数据模型和接口**不得写死账户数量**，必须支持 N 个账户。

统一资源隔离键：

```text
account_key
```

`account_key` 应来自稳定的账户绑定身份/指纹，不要求保存明文 account id。

### 2.2 多进程同步执行 / Multi-Process Synchronous Execution

目标不是让一个进程管理大量并发订单，而是允许多个独立策略进程分别拥有自己的同步 `ExecutionSession`：

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

必须允许：

```text
Account A / 0700.HK   -> WORKING
Account A / 510300.SH -> CANCELLING
```

同时存在。

必须阻止：

```text
Account A / 0700.HK execution-1 -> WORKING
Account A / 0700.HK execution-2 -> NEW EXECUTION
```

### 2.3 策略归属边界 / Strategy Ownership Boundary

Core **不负责**维护“哪个 symbol 属于哪个 strategy”的长期业务归属。

Core 只负责：

> 同一 `(account_key, symbol)` 只要上一笔 execution 尚未安全闭环，就禁止新的 execution lifecycle 开始。

---

## 3. API 兼容性合同 / Compatibility Contract

以下现有 public surfaces 必须保持 source-compatible，除非发现必须修正的明确缺陷且单独记录：

- `ExecutionRequest` 现有字段与语义；
- `ExecutionSnapshot` 现有字段；
- `BrokerPort.place_order/cancel_order/query_order/query_orders/execution_healthy`；
- `ExecutionGuard.verify_session/verify`；
- `ExecutionSession.submit/poll/cancel/reconcile/next_cycle`；
- `MiniQmtRuntime.submit/poll/cancel/next_cycle`；
- `before_broker_submit` / `before_broker_cancel` hooks。

新增 coordination API 应采用 additive 方式。

`ExecutionSession` 继续保持 **one-active-execution-at-a-time**。v0.4 的并发来自多个独立 runtime/session，而不是把单个 session 变成 multi-order engine。

建议新增但不强制具体命名：

```python
class ExecutionCoordinator(Protocol): ...
class CashRequirementEstimator(Protocol): ...
class AccountResourcePort(Protocol): ...
```

`AccountResourcePort` 建议与 `BrokerPort` 分离，以保持现有 fake/sim broker 的兼容性。

---

## 4. 特性分级 / Feature Priorities

### 4.1 P0 — Core Reliability / 核心可靠执行

| 中文特性 | English Feature | 重要性 | 要求 |
|---|---|---:|---|
| 持久化交易意图 | Durable Intent | ★★★★★ Critical | Required |
| 幂等执行 | Idempotent Execution | ★★★★★ Critical | Required |
| UNKNOWN 状态恢复 | UNKNOWN State Recovery | ★★★★★ Critical | Required |
| 崩溃与重启恢复 | Crash & Restart Recovery | ★★★★★ Critical | Required |
| 撤单/成交竞态处理 | Cancel/Fill Race Handling | ★★★★★ Critical | Required |
| 严格券商查询语义 | Strict Broker Query Semantics | ★★★★★ Critical | Required |
| 显式执行状态机 | Explicit Execution State Machine | ★★★★★ Critical | Required |
| 标的级执行串行化 | Per-Symbol Execution Serialization | ★★★★★ Critical | Required |
| 不同标的独立并发 | Independent Cross-Symbol Concurrency | ★★★★★ Critical | Required |

### 4.2 P1 — Safe Broker & Account Integration / 安全账户与券商接入

| 中文特性 | English Feature | 重要性 | 要求 |
|---|---|---:|---|
| 账户绑定与校验 | Account Binding & Verification | ★★★★☆ High | Required |
| 实盘/模拟盘隔离保护 | Live/Simulation Safety Gate | ★★★★☆ High | Required |
| Broker 接口抽象 | Broker Adapter Abstraction | ★★★★☆ High | Required |
| 回调隔离与串行事件处理 | Callback Isolation & Serial Event Processing | ★★★★☆ High | Required |
| Session ID 管理 | Session ID Management | ★★★☆☆ Medium | Required for shared-QMT multi-process |
| 共享资金原子预留 | Atomic Shared Cash Reservation | ★★★★★ Critical | Required for multi-writer same account |
| 保守交易资金估算 | Conservative Cash Requirement Estimation | ★★★★★ Critical | Required for BUY |
| 新鲜券商资金校验 | Fresh Authoritative Cash Verification | ★★★★★ Critical | Required for BUY |

### 4.3 P2 — Development Assurance / 开发期保障

| 中文特性 | English Feature | 重要性 | 要求 |
|---|---|---:|---|
| 状态机形式化验证 | Formal State-Machine Verification | ★★★☆☆ Medium | Keep |
| 源码/状态规范绑定校验 | Source/Spec Integrity Binding | ★★★☆☆ Medium | Keep |
| 故障注入测试 | Fault-Injection Tests | ★★★★☆ High | Keep |
| 跨进程竞争测试 | Cross-Process Contention Tests | ★★★★☆ High | Keep |

---

## 5. 核心设计原则 / Core Design Principles

### P-001 — Reliable Before Fast / 可靠性优先

低频个人交易首先保证：不重复下单、不误判撤单、不因重启丢失执行事实、不把查询失败当成“没有订单”。

### P-002 — Fail Closed / 失败关闭

当 Core 无法证明 execution 安全时，阻止新的 broker side effect，不猜测、不放行。

### P-003 — Broker Is Authoritative / 券商为订单现实权威

本地状态不能单独证明订单是否存在、成交或最终撤单成功；关键结果必须通过 broker query/reconciliation 确认。

### P-004 — Durable Before Side Effect / 先持久化后副作用

broker submit/cancel side effect 前必须已经持久化足以用于 crash recovery 的信息。

### P-005 — No Blind Resend / 禁止盲目重发

UNKNOWN、网络异常和进程异常都不能直接转化为新的 submit。

### P-006 — Concurrency by Independence / 仅协调真正冲突资源

不同标的不能被 execution-global lock 相互阻塞。跨进程只同步：

- `(account_key, symbol)` execution claim；
- account-level shared cash reservation；
- shared-QMT session-id lease。

---

## 6. 执行核心不变量 / Execution Invariants

### INV-001 — Durable Intent Before Submit

任何 broker submit side effect 前必须存在 durable intent。

### INV-002 — Idempotent Logical Order

同一个 logical order identity 在语义生命周期内不得重复提交。

### INV-003 — UNKNOWN Is Recoverable

`UNKNOWN` 只能进入 query/reconcile，不能解释为“broker 上没有订单”，也不能获得 resend permission。

### INV-004 — Restart Recovery Uses Durable Identity

重启后必须根据 durable journal/identity + broker authoritative query 恢复未闭环 execution，不能直接开始新周期。

### INV-005 — Cancel Request != Cancelled

cancel API 成功只表示撤单请求已发出/接受；最终 `CANCELLED` 必须 broker confirm。

### INV-006 — Fill Wins During Cancel Race

```text
CANCELLING + broker FILLED -> FILLED
```

成交事实优先。

### INV-007 — Query Ambiguity Fails Closed

`None`、exception、unknown raw status、ambiguous multiple match 不能解释为 empty/no-order。

### INV-008 — Callback Does Not Own Execution Authority

MiniQMT callback 只能产生 immutable observation，经 bounded serial event queue 进入执行线程；callback 不能直接 submit/cancel/journal/strategy mutation。

### INV-009 — Per-Symbol Execution Exclusivity

> At most one unresolved execution lifecycle may exist for the same `(account_key, symbol)` across processes.

第二个同账户同标的 request 必须在 broker side effect 前 fail closed。

### INV-010 — Independent Cross-Symbol Concurrency

> Executions on different symbols MUST NOT block each other solely because another execution is active.

因此 qmt-path-wide exclusive execution lock 不能作为最终唯一 production concurrency 模式。

---

## 7. Execution Finality / 执行终局性

Core 必须区分“状态名称”和“现实是否已安全闭环”。推荐 additive 模型：

```text
OPEN
RESOLVED
QUARANTINED
```

典型映射：

```text
ACCEPTED / WORKING / PARTIALLY_FILLED
PENDING_CANCEL / CANCELLING
UNKNOWN / CANCEL_REJECTED              -> OPEN

FILLED / CANCELLED / definitive REJECTED
proven-never-submitted                  -> RESOLVED

FAILED + broker reality still unresolved -> QUARANTINED
```

关键规则：

- `FAILED` 本身不能证明可以释放 symbol claim；
- `UNKNOWN` / `CANCEL_REJECTED` 必须保留 claim；
- 只有 `RESOLVED` 才能释放 `(account_key, symbol)`；
- `QUARANTINED` 必须阻止同标的新 execution，直到人工或权威 reconciliation 解决。

优先从现有 `TradeState + SafetyFacts` 推导 finality；除非确有必要，不为 finality 单独膨胀 `TradeState`。

---

## 8. Durable Cross-Process Coordination / 跨进程协调

第一版采用本机 SQLite，适配个人低频、多进程场景。

最小 durable schema 概念：

```text
symbol_claim
  account_key
  symbol
  execution_id/client_order_id
  finality
  created_at
  updated_at

cash_reservation
  account_key
  execution_id/client_order_id
  required_cash
  active
  created_at
  updated_at
```

同标 claim 必须通过真实原子事务/唯一约束保证：

```text
UNIQUE(account_key, symbol) for unresolved claim
```

进程内 `set()`、`threading.Lock()` 不足以满足要求。

推荐事务工具：

```sql
BEGIN IMMEDIATE;
```

或有等价原子性证明的实现。

---

## 9. Shared Cash Reservation / 共享资金预留

### 9.1 目的

共享资金 reservation 只解决：

> 多个 writer 在订单执行期间不得重复使用同一份 broker available cash。

Core 不复制券商结算系统，也不维护 `Settlement Pending / Settlement Finality`。

### 9.2 Conservative Cash Requirement / 保守资金需求

BUY 不能只按：

```text
qty * limit_price
```

预留。

必须支持：

```text
required_cash
=
max_order_notional
+ conservative_transaction_cost
+ broker_temporary_withholding_buffer
+ optional_fx_or_rounding_buffer
+ optional_safety_buffer
```

例如港股通可通过 estimator 纳入券商交易时可能高于最终实际费用的临时扣款 buffer。

**generic Core 不硬编码具体费率、税率、最低收费、港股通规则或 FX 规则。**

推荐接口：

```python
class CashRequirementEstimator(Protocol):
    def estimate(self, request, account_snapshot) -> CashRequirementEstimate:
        ...
```

Market/Account Adapter 负责具体费用/暂扣规则；Core 负责强制调用 estimator、强制 fail closed 和原子 reservation。

### 9.3 Fresh Authoritative Cash / 最新券商可用资金

每次新建 BUY reservation 必须基于**新鲜的 broker available_cash**：

```text
effective_available_cash
=
fresh_broker_available_cash
- sum(other_active_reservations_for_account)
```

要求：

```text
required_cash <= effective_available_cash
```

禁止用旧 cash snapshot，也禁止把“释放 reservation 的金额”直接加回本地 available cash。

### 9.4 Reservation Lifecycle / 预留生命周期

保持简单：

```text
RESERVED -> RELEASED
```

以下状态继续保持 reservation：

```text
SUBMITTED
ACCEPTED
WORKING
PARTIALLY_FILLED
PENDING_CANCEL
CANCELLING
CANCEL_REJECTED
UNKNOWN
QUARANTINED/unresolved failure
```

仅在 execution `RESOLVED`，或已证明 broker submit 从未发生时释放。

订单终态后释放本地 reservation；**下一笔订单仍必须重新查询 broker available_cash**。如果券商有临时扣款/冻结，它自然体现在新的 broker cash snapshot 中，不额外维护 settlement pending ledger。

### 9.5 Cash Invariants

**INV-CASH-001** — Every BUY must atomically reserve conservative maximum cash before broker submit.

**INV-CASH-002** — Every new BUY reservation must use a fresh authoritative broker available-cash snapshot.

**INV-CASH-003** — Effective cash equals fresh broker available cash minus all other active reservations for the same `account_key`.

**INV-CASH-004** — Reservation may be released only after execution finality is `RESOLVED` or submit is proven never to have happened.

**INV-CASH-005** — Releasing reservation MUST NOT directly increase locally assumed available cash.

---

## 10. Multi-Account Model / 多账户模型

从 v0.4 起所有共享资源必须以 `account_key` 隔离：

```text
cash pool          -> account_key
symbol claim       -> (account_key, symbol)
kill switch        -> account_key (if implemented)
session runtime    -> account binding + qmt path + session id
```

因此：

```text
Account A / 0700.HK
Account B / 0700.HK
```

是两个独立 execution claim，可以并发。

部署上推荐每账户一个 coordination DB 以增强物理隔离，例如：

```text
qec_coord_live_A.db
qec_coord_live_B.db
qec_coord_sim_A.db
```

但 schema 仍必须包含 `account_key`，不能假设一个 DB 永远只服务一个账户；统一多账户 DB 也应被模型允许。

---

## 11. Shared MiniQMT Runtime / 共享 QMT Runtime

保留当前 `exclusive` 模式作为 backward-compatible safety mode，同时新增明确的 shared mode，例如：

```text
runtime_lock_mode = exclusive | shared
```

shared mode 要求：

- 不使用 qmt-path-wide execution-exclusive mutex 阻塞所有 runtime；
- 每个 runtime/session 仍有自己的 local integrity protection；
- 不同进程使用不同 MiniQMT session ID；
- 同账户不同标的可并发；
- 同账户同标的由 durable symbol claim 阻塞；
- BUY 资金竞争由 shared cash reservation 阻塞。

安全实现顺序必须是：**先实现 coordination，再开放 shared runtime mode。**

---

## 12. Session ID Management / 会话 ID 管理

shared mode 下不得继续依赖无限随机 session id。

要求：

- caller-supplied exact `session_id` 继续支持；
- automatic mode 使用 bounded pool；
- 允许 deterministic/preferred candidate；
- collision/connect failure 只做有限 fallback；
- 不产生无限 session 文件增长；
- crashed process 不得永久占用 lease；
- OS-released per-session file lock 可作为 lightweight lease；
- 不假设 MiniQMT 存在 session allocate/release API。

策略代码不应直接承担 session-id 管理职责。

---

## 13. Core / Project / Shared Account 边界

### Core Owns

```text
execution lifecycle
durable intent
idempotency
UNKNOWN/restart recovery
cancel/fill race
strict broker query
execution finality
(account_key, symbol) claim
BrokerPort abstraction
MiniQMT account/session safety
callback isolation
```

### Shared Account Layer Owns

```text
atomic shared cash reservation
fresh authoritative cash verification
CashRequirementEstimator orchestration
optional account-wide exposure / kill switch
```

### Project / Strategy Owns

```text
why / when / how much to trade
signal generation
position sizing
VWAP / anchor
T+1 / settlement business rules
CorePosition / T-Lot / grid semantics
symbol-to-strategy assignment
strategy-specific risk rules
market-specific fee estimator configuration
```

---

## 14. Current Non-Goals / 当前非目标

v0.4 不实现：

- `order_stock_async()` execution path；
- central multi-strategy scheduler；
- RPC execution gateway；
- distributed coordination across machines；
- multi-order-in-one-`ExecutionSession`；
- HFT-level throughput；
- Smart Order Routing；
- complete shared position coordinator；
- strategy-specific TGrid/ETF/repo logic；
- broker/market fee rules hard-coded in generic Core；
- Settlement Pending / Settlement Finality cash ledger。

---

## 15. Required Acceptance Scenarios / 必须验收场景

1. **Duplicate prevention**：ambiguous submit -> restart -> unique broker match -> broker submit count remains 1。
2. **UNKNOWN recovery**：`SUBMITTED -> UNKNOWN -> WORKING -> FILLED`。
3. **Cancel/fill race**：`WORKING -> cancel -> CANCELLING -> broker FILLED -> FILLED`。
4. **Cancel-rejected recovery**：`WORKING -> CANCEL_REJECTED -> UNKNOWN -> WORKING -> FILLED`，同一 execution，无 resend。
5. **Restart recovery**：active/cancel/UNKNOWN 状态重启后通过 durable identity + broker query 恢复。
6. **Same-symbol blocking**：Process A 在 `(Account X, Symbol A)` active；Process B 同 key request 必须在 broker call 前被阻止。
7. **Cross-symbol concurrency**：Process A `(X,A)` active；Process B `(X,B)` active，两者必须可同时存在。
8. **Cross-account independence**：`(Account A,0700)` 与 `(Account B,0700)` 可以并发。
9. **Shared cash race**：fresh cash 100k；两个进程请求 60k + 50k reservation；不能都成功导致超额。
10. **Conservative estimator**：配置 fee/withholding buffer 后 reservation 必须大于纯 notional。
11. **Fresh cash after terminal**：订单 RESOLVED 后释放 reservation；下一笔必须重新 query broker cash，而不是本地把 reservation 金额加回。
12. **Query ambiguity**：query None/exception/unknown raw status 不产生 resend permission。
13. **Quarantine**：broker reality 无法解析时保留 symbol claim，并阻塞新同标 execution。
14. **Shared runtime**：两个 runtime 同 qmt path、不同 session id、不同 symbol 能在 fake/cross-process 测试中共存。
15. **Session collision**：session-id collision/connect failure 只能有限 fallback，不得无限 retry。
16. **Backward compatibility**：现有 0.3.1 strategy-facing API 测试继续通过。

---

## 16. Verification & Release Gate / 验证与发布门槛

目标 release：`0.4.0` 或清晰记录的等价版本。

发布/交接前必须至少完成：

```text
full pytest
compileall src/tests
formal verifier
Python 3.9 compatibility
wheel build + clean install + out-of-tree verifier
Windows mutex/session-lease probes
cross-process same-symbol contention test
cross-process different-symbol concurrency test
cross-process shared-cash atomicity test
no real or simulation QMT order/cancel calls during implementation validation
```

任何 state/event 修改都必须同步更新 formal verifier 和 refinement tests。

Live execution 继续默认 fail closed，不得弱化 account binding、live double gate、disconnect recovery、event-queue health 或 project evidence checks。

---

## 17. 实现顺序 / Implementation Order

```text
Phase 0 — Freeze current reliable execution baseline
    |
    v
Phase 1 — Execution Finality
          + durable (account_key, symbol) claim
    |
    v
Phase 2 — Atomic Shared Cash Reservation
          + Conservative CashRequirementEstimator
          + Fresh Authoritative Broker Cash Verification
    |
    v
Phase 3 — Shared MiniQMT runtime mode
          + bounded Session ID management
          + remove qmt-path-wide exclusivity in shared mode
    |
    v
Phase 4 — Cross-process integration tests / formal refinement
    |
    v
Phase 5 — TGrid integration and regression
```

原则：**先建立 fail-closed 的资源协调，再开放同 qmt path 多进程并发。**

---

## 18. 冻结与变更控制 / Freeze & Change Control

本文件是 `qmt-execution-core` v0.4 的 canonical formal specification。

从本次冻结开始：

1. v0.4 实现以本文件的 P0/P1、invariants、concurrency model 和 acceptance scenarios 为准；
2. 不得重新引入已经明确排除的 `Settlement Pending / Settlement Finality` 资金模型；
3. 不得把 market/broker fee rules 硬编码进 generic Core；
4. 不得通过 qmt-path 全局独占锁破坏同账户不同标的跨进程并发；
5. 不得为了并发把单个 `ExecutionSession` 扩成复杂 multi-order engine；
6. 修改下列语义必须显式升级规格版本，不能静默修改 v0.4：
   - strategy-facing API compatibility contract；
   - execution state/finality semantics；
   - `(account_key, symbol)` claim semantics；
   - shared cash reservation semantics；
   - live/simulation safety boundary；
   - shared MiniQMT runtime/session-id model。

不改变上述语义的纯文档修正可以作为 v0.4 editorial amendment，但 commit message 必须明确 documentation-only。

---

## 19. 一句话规格 / One-Line Specification

> **qmt-execution-core provides durable, idempotent, recoverable and fail-closed execution for MiniQMT, while allowing independent cross-symbol concurrency, preventing overlapping unresolved executions on the same account and symbol, and atomically coordinating shared account cash across independent strategy processes.**

中文：

> **qmt-execution-core 为 MiniQMT 提供持久化、幂等、可恢复、失败关闭的可靠执行能力；允许同账户不同标的独立并发，禁止同账户同标的重叠未闭环执行，并通过跨进程原子资金预留安全共享账户资金池。**