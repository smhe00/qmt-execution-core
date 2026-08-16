# MiniQMT / XtQuant Adapter Profile — v0.4

本文定义 MiniQMT/XtQuant broker observation 如何归一化进入 Generic Execution Core。Generic state machine 不直接 import 或解释 `xtquant` 常量。

## 1. 分层

```text
XtQuant raw API / XtOrder
        |
        v
MiniQmtBrokerAdapter
        |
        v
BrokerOrderStatus
        |
        v
event_for_observation(current_state, status)
        |
        v
Generic TradeState
```

策略代码不得直接按 `50 / 55 / 255` 等 QMT raw status 分支。

---

## 2. order_status 归一化

| QMT raw status | Value | Core normalized | 语义 |
|---|---:|---|---|
| `ORDER_UNREPORTED` | 48 | `ACCEPTED` | 已有订单对象，尚未报送完成 |
| `ORDER_WAIT_REPORTING` | 49 | `ACCEPTED` | 等待报送 |
| `ORDER_REPORTED` | 50 | `WORKING` | 活动订单 |
| `ORDER_REPORTED_CANCEL` | 51 | `CANCEL_PENDING` | 撤单中 |
| `ORDER_PARTSUCC_CANCEL` | 52 | `CANCEL_PENDING` | 部分成交 + 撤单中 |
| `ORDER_PART_CANCEL` | 53 | `PARTIAL_CANCELLED` | 部分成交，剩余已确认取消 |
| `ORDER_CANCELED` | 54 | `CANCELLED` | 已取消终态 |
| `ORDER_PART_SUCC` | 55 | `PARTIALLY_FILLED` | 部分成交，剩余仍活动 |
| `ORDER_SUCCEEDED` | 56 | `FILLED` | 全部成交 |
| `ORDER_JUNK` | 57 | `REJECTED` | 券商拒绝 |
| `ORDER_UNKNOWN` | 255 | `UNKNOWN` | 未解决 |
| 未识别值/类型 | other | `UNKNOWN` | fail closed |

矛盾 payload 也归一化为 `UNKNOWN`，例如 `ORDER_SUCCEEDED` 但 `filled_qty != qty`。

---

## 3. 同步 order_stock() 语义

0.4 仍以同步执行为主：

```text
positive plain-int order id
 -> persist broker order id
 -> SUBMIT_ACCEPTED
 -> authoritative query

-1
 -> BrokerSubmissionRejected
 -> REJECTED

exception / malformed / unexpected nonpositive
 -> BrokerSubmissionAmbiguous
 -> UNKNOWN
```

positive order id 不等于 WORKING/FILLED，最终状态仍由 query 决定。

`order_stock_async()` 不属于 0.4 execution path。

---

## 4. cancel_order_stock() 语义

```text
return 0
 -> cancel request accepted for sending
 -> CANCELLING
 -> mandatory re-query

return failure / exception
 -> CANCEL_REJECTED
 -> mandatory re-query
```

撤单请求成功永远不能直接归一化为 `CANCELLED`。

订单在撤单过程中成交时，broker `FILLED` 是最终事实。

---

## 5. Strict Query

`query_stock_order()` / `query_stock_orders()` 采用 bounded retry：

```text
non-None usable result -> broker observation
None                   -> ambiguous, retry
exception              -> ambiguous, retry
bounded failure        -> BrokerQueryAmbiguous
```

`None` 不会被静默转换成“没有订单”。

---

## 6. Recovery Identity

当 submit outcome UNKNOWN 且没有捕获 broker order id 时，Core 查询 managed orders 并匹配 durable local identity：

```text
order_remark
symbol
side
qty
```

必须得到唯一匹配；0 或多个匹配都 fail closed。

---

## 7. Account Resource Queries

`MiniQmtBrokerAdapter` 还提供账户只读事实，例如：

```text
query_asset()
query_positions()
query_trades()
```

0.4 通过独立 `AccountResourcePort` 使用 `query_asset()` 支持 coordinated BUY，而不扩大所有 `BrokerPort` 实现的强制接口。

Shared BUY 每次都读取 fresh authoritative broker cash，再与 SQLite active reservations 合并判断。

---

## 8. Callback Isolation

```text
on_stock_order  -> QmtOrderObserved
on_stock_trade  -> QmtTradeObserved
on_disconnected -> QmtBrokerDisconnected
on_order_error  -> QmtOrderErrorObserved
on_cancel_error -> QmtCancelErrorObserved
```

Callback 不会：

- 直接改变策略状态；
- 直接修改 journal/coordination DB；
- submit/cancel；
- blind retry UNKNOWN；
- 清除安全 halt。

所有 observation 进入 bounded serial EventQueue。

---

## 9. Disconnect / Recovery

断线首先使 execution health 失效。

恢复：

```text
transport reconnect
→ exact bound account verification
→ subscribe
→ strict broker query
→ durable execution reconciliation
→ project session evidence verification
→ runtime recovery complete
→ live mode fresh confirmation
```

仅收到 `ACCOUNT_STATUS_OK` 或 transport reconnect 不足以重新开放订单权限。

---

## 10. Multi-runtime shared mode

0.4 支持同一个 QMT userdata path 上多个独立 runtime：

```text
runtime_lock_mode="shared"
```

此时：

- 不使用 qmt-path-wide exclusive runtime mutex；
- 每个 runtime 获得不同的 bounded session-id lease；
- 同 `(account_key,symbol)` 冲突由 SQLite claim 阻止；
- 同账户 BUY cash race 由 SQLite reservation 阻止；
- 不同标的允许并发。

默认仍为：

```text
runtime_lock_mode="exclusive"
```

保持旧项目的最保守行为。

---

## 11. Dependency Boundary

`src/qmt_execution_core/miniqmt/` 不在模块 import 时强制 import `xtquant`。

真实 MiniQMT 环境通过 lazy import 或 dependency injection 提供：

- trader factory；
- StockAccount；
- constants；
- callback base。

因此 CI 可以完全使用 fake XtQuant，不触发真实/模拟 QMT 订单。
