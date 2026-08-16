# qmt-execution-core 0.4 架构

## 1. 依赖规则

```text
Project Strategy
      |
      | ExecutionRequest
      v
ExecutionSession / CoordinatedExecutionSession
      |
      v
BrokerPort
      |
      +---- MiniQmtBrokerAdapter
      +---- FakeBroker
      +---- future broker adapter
```

Generic Core 不直接 import `xtquant`；MiniQMT 相关依赖只存在于 `miniqmt/` 适配层，并允许 dependency injection。

---

## 2. Generic Core 职责

- explicit execution state machine；
- durable intent / cancel intent；
- cross-cycle client id / order remark idempotency；
- crash-safe journal；
- per-session execution mutex；
- query/recovery rules；
- normalized broker DTO；
- ExecutionGuard evidence contract；
- common hard-limit guards；
- execution finality；
- optional cross-process account coordination；
- formal explicit-state/refinement verification。

Core 不负责策略信号、组合配置或市场专属交易逻辑。

---

## 3. v0.4 并发模型

`ExecutionSession` 仍保持 one-active-execution-at-a-time。

并发来自独立 process/runtime/session：

```text
Process A                                  Process B
Strategy A                                 Strategy B
   |                                          |
   v                                          v
ExecutionSession A                       ExecutionSession B
Journal A / Mutex A                      Journal B / Mutex B
   |                                          |
   +------------------+-----------------------+
                      |
                      v
          SQLite Shared Coordination
          - (account_key, symbol) claim
          - shared BUY cash reservation
                      |
                      v
                 MiniQMT Account
```

### 同账户同标的

```text
(account_key, symbol)
```

最多一个 unresolved execution。

### 同账户不同标的

可以并发：

```text
Account A / 0700.HK   -> WORKING
Account A / 510300.SH -> CANCELLING
```

### 不同账户同标的

也可以并发，因为 `account_key` 不同。

---

## 4. Shared Account Coordination

共享协调是一个窄的 cross-process resource layer，不是中央 OMS。

默认实现：

```text
SQLiteExecutionCoordinator
```

使用 SQLite 原子事务（`BEGIN IMMEDIATE`）保护：

```text
symbol_claim
cash_reservation
```

业务数据库、strategy state、position model 不进入这个 DB。

### Symbol Claim

唯一冲突粒度：

```text
(account_key, symbol)
```

claim 只有在 `ExecutionFinality.RESOLVED` 时释放。

### Shared Cash

BUY：

```text
fresh broker available cash
- other active same-account reservations
>= conservative required cash
```

检查与 reservation 写入在一个写事务中完成。

SELL 第一版不做共享现金预留。

---

## 5. Execution Finality

状态名和资源是否可以释放是两个概念。

```text
OPEN
RESOLVED
QUARANTINED
```

典型映射：

```text
WORKING / PARTIAL / UNKNOWN / CANCEL_REJECTED -> OPEN
FILLED / CANCELLED / definitive REJECTED       -> RESOLVED
FAILED + unresolved_order                      -> QUARANTINED
FAILED + broker submit proven absent           -> RESOLVED
```

`PRE_BROKER_ABORTED` 用于机械记录同步 pre-broker failure：broker side effect 已证明没有发生。

---

## 6. Submit 执行顺序

协调模式固定顺序：

```text
Guard / PRE_CHECK
      |
      v
Core Durable Intent
      |
      v
Shared Coordination
      |
      +-- claim(account_key, symbol)
      +-- fresh account cash query for BUY
      +-- conservative cash reservation
      |
      v
Project before_broker_submit sidecar
      |
      v
BrokerPort.place_order
```

任何前置阶段失败都不能触发 broker submit。

Project sidecar 同步失败时：

- broker call count 必须为 0；
- 已取得的 shared resources 释放；
- `PRE_BROKER_ABORTED` 记录 proven-never-submitted；
- 原异常继续传播。

---

## 7. CashRequirementEstimator

Core 只强制“保守估算”，不写死市场费率。

```text
CashRequirementEstimator
      |
      v
CashRequirementEstimate
```

估算可包含：

- max order notional；
- transaction cost buffer；
- temporary broker withholding buffer；
- FX / rounding buffer；
- safety buffer。

港股通、A 股等具体规则属于市场/账户 policy。

---

## 8. MiniQMT Runtime 职责

- lazy import MiniQMT environment；
- QMT userdata path validation；
- fingerprint-only account binding；
- exact account type/status selection；
- `start -> connect -> subscribe` lifecycle；
- raw QMT status normalization；
- strict query semantics；
- immutable callback bridge；
- bounded serial callback event queue；
- disconnect invalidation；
- reconnect/account/subscription/reconcile recovery；
- live config enable + runtime-only confirmation gate；
- exclusive/shared runtime mode；
- bounded MiniQMT session-id lease in shared mode。

---

## 9. Runtime Locking

### exclusive（默认，兼容 0.3）

```text
qmt_path
   |
   v
runtime-wide ExecutionMutex
```

同一 qmt path 一次只允许一个 runtime。

### shared（0.4）

不获取 qmt-path-wide exclusive runtime mutex。

替代安全边界：

```text
per-session journal mutex
+ bounded session-id OS lease
+ (account_key,symbol) durable claim
+ account cash reservation transaction
```

因此 shared mode 允许不同策略 process 共存，但不是“无锁运行”。

---

## 10. Session ID

`BoundedSessionIdAllocator`：

- caller supplied exact id 仍支持；
- 自动模式使用有限 pool；
- preferred candidate 稳定可复现；
- 冲突/connect failure 只有有限 fallback；
- 每个 session id 通过 OS file lock lease 保护；
- process crash 后 lease 随 OS lock 自动释放。

MiniQMT session id 不被建模为 broker-issued lease。

---

## 11. Callback 并发模型

```text
QMT callback threads
       |
       | immutable observation only
       v
bounded SerialEventQueue
       |
       v
single callback/event handler
```

Callback 不是 execution authority，也不是 restart recovery authority。

---

## 12. Recovery Authority

```text
Durable Journal + Authoritative Broker Query
```

UNKNOWN 不允许 blind resend。

如果 recovery 无法确认 broker reality：

```text
FAILED + unresolved_order=True
        |
        v
QUARANTINED
```

同标 claim 继续保留。

---

## 13. Live Execution Authority

```text
environment == live
AND trusted config: live_trading_enabled == true
AND runtime-only confirmation token matches configured SHA-256
AND transport/account/subscription are healthy
AND durable recovery is complete
AND event queue is healthy
AND project precheck evidence passes
AND shared coordination succeeds when enabled
```

任何单一条件都不足以启用实盘新订单。

---

## 14. 多账户

共享资源按 `account_key` 隔离。

`account_key` 使用已有 binding identity 的：

```text
environment + account_type + account_id_sha256
```

派生，不要求 coordination DB 存明文账户号。

一个 SQLite DB 可以承载多个账户；个人部署也可以每账户一个 DB，以增强物理隔离。

---

## 15. Non-Goals

0.4 不引入：

- async order submit path；
- one-session multi-order engine；
- central scheduler；
- RPC execution gateway；
- distributed/multi-machine coordination；
- shared position coordinator；
- strategy-specific market fee logic。

设计原则仍是：

> **进程级并发 + 单 session 同步执行 + 只集中必须跨进程原子协调的账户资源。**
