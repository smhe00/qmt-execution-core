# qmt-execution-core 0.4 状态机规格

本文是 `qmt-execution-core` 的 broker-neutral、strategy-neutral 执行状态机说明。正式产品边界与资源协调要求以 [v0.4 冻结规格](CORE_SPEC_V0_4_RESOURCE_COORDINATION.md) 为准。

## 1. TradeState

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

状态机仍保持 one-active-execution-at-a-time。跨标的并发由多个独立 session/process 提供，而不是在单个状态机里管理多订单。

---

## 2. 关键执行路径

正常提交：

```text
IDLE
 -> WAIT_TRIGGER
 -> TRIGGER
 -> PRE_CHECK
 -> SUBMITTED
 -> ACCEPTED
 -> WORKING / PARTIALLY_FILLED
 -> FILLED / CANCELLED / REJECTED
```

协调模式下，`SUBMITTED` 表示 Core durable intent 已经写入；实际 broker side effect 之前仍有两个同步阶段：

```text
Core Durable Intent
      |
      v
SUBMITTED
      |
      +-- shared coordination
      |      - symbol claim
      |      - BUY cash reservation
      |
      +-- project before_broker_submit sidecar
      |
      v
BrokerPort.place_order()
```

---

## 3. Pre-broker 结果

### PRE_BROKER_REJECTED

用于 Core/shared coordination 的**正常 fail-closed 本地拒绝**，例如：

- 同 `(account_key, symbol)` 已被另一个 unresolved execution 占用；
- conservative BUY cash 不足；
- coordinated BUY 缺少必须的 estimator。

路径：

```text
SUBMITTED
 -> PRE_BROKER_REJECTED
 -> REJECTED
```

并且：

```text
broker_invoked = false
submitted_once = false
unresolved_order = false
```

### PRE_BROKER_ABORTED

用于同步 pre-broker hook 的异常失败，例如 project sidecar 持久化失败：

```text
SUBMITTED
 -> PRE_BROKER_ABORTED
 -> FAILED
```

但它与 broker ambiguity 不同：

```text
submitted_once = false
unresolved_order = false
terminal_order_confirmed = true
ExecutionFinality = RESOLVED
```

因此 `FAILED` 不能单独用于判断资源是否可释放。

---

## 4. Broker submit 结果

```text
positive broker order id
 -> SUBMIT_ACCEPTED
 -> ACCEPTED
 -> authoritative poll

broker definitive rejection
 -> SUBMIT_REJECTED
 -> REJECTED

ambiguous exception / ambiguous result
 -> SUBMIT_AMBIGUOUS
 -> UNKNOWN
```

`UNKNOWN` 永远不能触发 blind resend。

---

## 5. UNKNOWN / Recovery

`UNKNOWN` 是 recoverable state，不是“没有订单”。

允许通过权威 broker query/reconciliation 恢复到：

```text
ACCEPTED
WORKING
PARTIALLY_FILLED
CANCELLING
FILLED
CANCELLED
REJECTED
```

如果恢复仍无法确定 broker reality：

```text
UNKNOWN
 -> RECOVERY_FAILED
 -> FAILED
```

此时 `unresolved_order=True`，所以：

```text
ExecutionFinality = QUARANTINED
```

同标 symbol claim 必须继续保持。

---

## 6. Cancel 语义

撤单请求不是最终取消：

```text
WORKING / PARTIALLY_FILLED
 -> CANCEL_REQUESTED
 -> PENDING_CANCEL
 -> CANCEL_SENT
 -> CANCELLING
 -> authoritative broker query
```

撤单 API 返回失败：

```text
PENDING_CANCEL
 -> CANCEL_REQUEST_REJECTED
 -> CANCEL_REJECTED
 -> mandatory query/recovery
```

`CANCEL_REJECTED` 仍是 recoverable state。

撤单过程中如果订单成交：

```text
CANCELLING -> ORDER_FILLED -> FILLED
```

成交事实优先。

---

## 7. ExecutionFinality

0.4 在 `TradeState + SafetyFacts` 之上推导：

```text
OPEN
RESOLVED
QUARANTINED
```

典型语义：

```text
WORKING / PARTIALLY_FILLED / UNKNOWN / CANCEL_REJECTED -> OPEN
FILLED / CANCELLED / REJECTED                         -> RESOLVED
FAILED + unresolved_order=True                        -> QUARANTINED
FAILED + unresolved_order=False                       -> RESOLVED
```

资源协调层只有在 `RESOLVED` 时才释放 `(account_key, symbol)` claim。

---

## 8. SafetyFacts 关键事实

状态机持续追踪：

```text
environment_verified
account_verified
broker_snapshot_verified
position_verified
cash_verified
quote_verified
intent_persisted
reservation_persisted
submitted_once
unresolved_order
terminal_order_confirmed
cancel_intent_persisted
```

重要不变量：

- broker submit 必须有 durable intent/reservation evidence；
- unresolved order 不得回到新订单路径；
- cancel path 必须先有 durable cancel intent；
- `UNKNOWN` 必须保持 unresolved evidence；
- successful terminal state 必须有 broker confirmation；
- pre-broker local reject/abort 不得伪造 `submitted_once=True`。

---

## 9. Formal Verification

`verify_state_machine()` 对显式状态空间做 fixed-point exhaustive reachability，检查：

- 所有声明 state 可达；
- 所有声明 transition 可达；
- 每个 reachable non-terminal state 有 terminal path；
- 没有 reachable invariant violation；
- UNKNOWN 没有 blind resend/new-order edge；
- v0.4 finality refinement 一致；
- protected execution sources 完整并参与 SHA-256 binding。

任何 state/event 变化都必须同步更新 verifier 与 refinement tests。
