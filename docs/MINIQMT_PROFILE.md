# MiniQMT / XtQuant Adapter Profile

This document defines how MiniQMT/XtQuant broker observations are normalized into the generic execution core.
The generic state machine never imports or interprets `xtquant` constants directly.

Official API reference used for this profile: `https://dict.thinktrader.net/nativeApi/xttrader.html`.

## 1. Layering

```text
XtQuant raw API / XtOrder
        ↓
MiniQmtBrokerAdapter
        ↓
BrokerOrderStatus
        ↓
event_for_observation(current_state, status)
        ↓
Generic TradeState
```

Project strategy code must not branch on raw QMT values such as `50`, `55`, or `255`.

## 2. QMT order_status normalization

| Raw status | Value | Normalized status | Generic meaning |
|---|---:|---|---|
| `ORDER_UNREPORTED` | 48 | `ACCEPTED` | broker has an order object, not yet reported |
| `ORDER_WAIT_REPORTING` | 49 | `ACCEPTED` | waiting to report |
| `ORDER_REPORTED` | 50 | `WORKING` | active order |
| `ORDER_REPORTED_CANCEL` | 51 | `CANCEL_PENDING` | cancel in progress |
| `ORDER_PARTSUCC_CANCEL` | 52 | `CANCEL_PENDING` | partial fill + cancel in progress |
| `ORDER_PART_CANCEL` | 53 | `PARTIAL_CANCELLED` | partial fill + remaining quantity cancelled |
| `ORDER_CANCELED` | 54 | `CANCELLED` | cancelled terminal |
| `ORDER_PART_SUCC` | 55 | `PARTIALLY_FILLED` | partial fill, remaining active |
| `ORDER_SUCCEEDED` | 56 | `FILLED` | filled terminal |
| `ORDER_JUNK` | 57 | `REJECTED` | rejected/junk terminal |
| `ORDER_UNKNOWN` | 255 | `UNKNOWN` | unresolved |
| any unrecognized value/type | other | `UNKNOWN` | fail closed |

### Critical distinctions

```text
52 PARTSUCC_CANCEL
= filled_qty > 0 + cancel still pending
= unresolved

53 PART_CANCEL
= filled_qty > 0 + remaining quantity confirmed cancelled
= terminal cancelled, preserve filled_qty

55 PART_SUCC
= filled_qty > 0 + remaining quantity still active
= unresolved partial fill
```

The adapter performs consistency checks: contradictory payloads (for example status 56 with `filled_qty != qty`) are normalized to `UNKNOWN`.

## 3. `order_stock()` semantics

The adapter calls synchronous `order_stock()` through dependency injection.

Normalized execution rules:

```text
positive plain-int order id
→ persist broker order id
→ SUBMIT_ACCEPTED / ACCEPTED
→ query broker order status

-1
→ BrokerSubmissionRejected
→ REJECTED

exception / non-int / unexpected nonpositive result
→ BrokerSubmissionAmbiguous
→ UNKNOWN
```

A positive order id is not a claim that the order is already `WORKING` or `FILLED`.

## 4. `cancel_order_stock()` semantics

```text
return 0
→ cancel request was accepted for sending
→ CANCELLING
→ mandatory re-query

return -1 / other failure / exception
→ CANCEL_REJECTED observation
→ mandatory re-query of the original order
```

`0` must never be normalized directly to `CANCELLED`.
The order can fill while a cancel request is in flight.

## 5. Strict query semantics

The adapter retries `query_stock_order()` / `query_stock_orders()` a bounded number of times.

```text
non-None result = usable broker response
None            = ambiguous, retry
exception       = ambiguous, retry
bounded failure = BrokerQueryAmbiguous
```

In particular, `None` is never silently converted to an empty order list in the execution core.

## 6. Recovery identity

If submit outcome is unknown and the broker order id was not captured, the core queries all managed orders and matches the **durable local identity**:

```text
order_remark
symbol
side
qty
```

Exactly one match is required.
A caller-provided temporary remark is not recovery authority.

## 7. Callback isolation

`QmtCallbackBridge` converts broker callbacks into immutable observations only:

```text
on_stock_order  → QmtOrderObserved
on_stock_trade  → QmtTradeObserved
on_disconnected → QmtBrokerDisconnected
on_order_error  → QmtOrderErrorObserved
on_cancel_error → QmtCancelErrorObserved
```

Callbacks do not:

- mutate state-machine state;
- write the journal;
- release reservations;
- send/cancel orders;
- retry UNKNOWN submissions;
- clear a safety halt.

A production integration should enqueue these observations onto a single execution/event thread.

## 8. Disconnect model

`MiniQmtBrokerAdapter.mark_disconnected()` makes `execution_healthy()` false and therefore blocks new execution requests.

A future production session bootstrap must not restore order capability merely because transport reconnects. The production recovery sequence should be:

```text
transport reconnect
→ exact bound account verification
→ subscribe
→ strict broker query
→ reconcile durable state
→ runtime reconfirmation
→ restore new-order capability
```

That production session/account-binding layer is intentionally outside v0.1.

## 9. Refinement test matrix

Minimum adapter tests:

```text
48 → ACCEPTED
49 → ACCEPTED
50 → WORKING
51 → CANCEL_PENDING
52 + valid partial fill → CANCEL_PENDING
53 + valid partial fill → PARTIAL_CANCELLED
54 → CANCELLED
55 + valid partial fill → PARTIALLY_FILLED
56 + full fill → FILLED
57 → REJECTED
255 → UNKNOWN
unexpected raw status → UNKNOWN
positive order_stock id → accepted id, not fill claim
order_stock -1 → definitive reject
submit exception → ambiguous/UNKNOWN
query None → ambiguous, not empty
cancel 0 → request accepted, not CANCELLED
partial fill + cancel → fill preserved
```

## 10. Dependency boundary

`src/qmt_execution_core/miniqmt/` intentionally does not import `xtquant` at module import time.
Production code supplies an already-constructed trader/account and numeric QMT order configuration.
This keeps CI and generic tests independent of a MiniQMT installation and allows other broker adapters to reuse the same execution core.
