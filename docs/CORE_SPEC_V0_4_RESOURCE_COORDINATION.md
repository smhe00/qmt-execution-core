# qmt-execution-core v0.4 Resource Coordination Specification

Status: ARCHITECT APPROVED FOR IMPLEMENTATION

Baseline: qmt-execution-core 0.3.1 @ `937e6a4a1cbd54df960f9bde3ca2e91d6bc19c79`

Target use case: a personal low-frequency trading system with one QMT live account and one simulation account, where several independent strategy processes may run concurrently. Different symbols may execute concurrently on the same account; the account cash pool is shared.

## 1. Product boundary

`qmt-execution-core` remains a reliable execution layer, not a strategy framework, scheduler, portfolio manager, central OMS, RPC gateway, or HFT engine.

The external strategy-facing execution shape remains synchronous:

```python
request = ExecutionRequest(...)
snapshot = runtime.submit(request)
snapshot = runtime.poll()
snapshot = runtime.cancel()
```

Async MiniQMT order submission is explicitly out of scope for v0.4.

## 2. Compatibility contract

The following existing public surfaces MUST remain source-compatible unless a defect makes that impossible and is separately documented:

- `ExecutionRequest` fields and semantics;
- `ExecutionSnapshot` existing fields;
- `BrokerPort.place_order/cancel_order/query_order/query_orders/execution_healthy`;
- `ExecutionGuard.verify_session/verify`;
- `ExecutionSession.submit/poll/cancel/reconcile/next_cycle`;
- `MiniQmtRuntime.submit/poll/cancel/next_cycle`;
- existing `before_broker_submit` / `before_broker_cancel` lifecycle hooks.

New coordination APIs should be additive. Existing single-writer callers must continue to work without adopting shared-account coordination.

`ExecutionSession` remains one-active-execution-at-a-time. v0.4 concurrency is achieved by multiple independent runtimes/sessions, not by turning one session into a multi-order engine.

## 3. Required invariants

### INV-C01 — Same-symbol execution exclusivity

At most one unresolved execution lifecycle may exist for the same `(account_key, symbol)` across processes.

A second request for the same account and symbol must fail closed before any broker submit side effect.

### INV-C02 — Cross-symbol concurrency

Executions on different symbols must not block each other solely because another execution is active.

Example that MUST be supported:

```text
Process A / Account X / 0700.HK   -> WORKING
Process B / Account X / 510300.SH -> CANCELLING
```

The current qmt-path-wide exclusive runtime mutex therefore cannot remain the only production concurrency mode.

### INV-C03 — No regression of reliable execution semantics

The existing guarantees remain mandatory:

- durable intent before broker submit;
- idempotency and no identity reuse;
- UNKNOWN is recoverable and never permission to resend;
- restart recovery is broker-query based;
- cancel request acceptance is not terminal cancellation;
- fill during cancel wins as `FILLED`;
- strict query ambiguity fails closed;
- callbacks do not own execution authority.

### INV-C04 — Execution finality is distinct from state name

Introduce an explicit execution-finality concept, preferably additive and derived from state + `SafetyFacts` rather than expanding the existing state machine unless expansion is demonstrably cleaner.

Required semantic classes:

```text
OPEN
RESOLVED
QUARANTINED
```

Examples:

```text
WORKING / PARTIALLY_FILLED / UNKNOWN / CANCEL_REJECTED -> OPEN
FILLED / CANCELLED / definitive REJECTED                -> RESOLVED
FAILED with unresolved broker reality                   -> QUARANTINED
pre-submit failure with broker side effect proven zero  -> RESOLVED
```

`FAILED` by itself MUST NOT imply that a same-symbol claim can be released.

### INV-C05 — Symbol claim release follows execution finality

Release `(account_key, symbol)` only when execution finality is `RESOLVED`.

`UNKNOWN`, `CANCEL_REJECTED`, and unresolved `FAILED` must retain the claim.

### INV-C06 — Shared BUY cash reservation is atomic and pre-submit

When multiple writable processes share one account cash pool, BUY execution must atomically reserve conservative required cash before broker submit.

```text
atomic reservation COMMIT
        -> broker BUY submit
```

Two processes must never both consume the same available cash snapshot.

### INV-C07 — Conservative cash requirement

Cash reservation MUST NOT be defined as only `qty * limit_price`.

Introduce a broker-neutral, market/account-specific estimator interface such as:

```python
class CashRequirementEstimator(Protocol):
    def estimate(self, request, account_snapshot) -> CashRequirementEstimate:
        ...
```

The estimate must be able to include:

```text
max order notional
+ conservative transaction costs
+ broker/settlement temporary withholding buffer
+ optional FX / rounding buffer
```

The core MUST NOT hard-code Hong Kong Stock Connect, A-share, tax, commission, FX, or broker-specific fee rules. Such rules belong in an injected estimator/policy.

For live coordinated BUY execution, absence of a required estimator/policy must fail closed rather than silently falling back to notional-only reservation.

### INV-C08 — Execution finality and settlement finality are independent

A broker order may be execution-final while some cash is still temporarily unavailable.

Define a settlement-reservation lifecycle, e.g.:

```text
HELD_PRE_SUBMIT
HELD_ACTIVE
SETTLEMENT_PENDING
RELEASED
```

Key rule:

> Execution Finality controls symbol-claim release; Settlement Finality controls cash-reservation release.

`FILLED`, `CANCELLED`, or `REJECTED` must not mechanically release all shared cash if authoritative account/broker evidence still shows cash frozen, temporarily withheld, or pending later return.

This is required for channels where broker debit can conservatively exceed final fees and excess cash may be returned later (for example, some Hong Kong Stock Connect settlement/fee handling).

### INV-C09 — Partial-fill settlement is conservative

For partial fill + cancel or similar outcomes, shared cash reservation must be recomputed conservatively from:

- the settled/pending requirement for the filled portion;
- the authoritative release status of the unfilled portion;
- any still-pending broker withholding.

Do not infer available cash merely from order terminal state.

## 4. Coordination architecture

Preferred decomposition:

```text
ExecutionSession / MiniQmtRuntime
        |
        +-- existing reliable order lifecycle
        |
        +-- optional ExecutionCoordinator
                 |
                 +-- cross-process symbol claim
                 +-- shared cash reservation
                 +-- settlement reservation state
```

Recommended additive protocols/types:

- `ExecutionFinality`;
- `SettlementState`;
- `ExecutionCoordinator`;
- `CashRequirementEstimator`;
- `CashRequirementEstimate`;
- `AccountResourcePort` (separate from existing `BrokerPort` if that preserves compatibility better).

The exact class names may differ, but the responsibility split and invariants must hold.

## 5. Durable cross-process store

Provide a generic SQLite-backed coordination implementation suitable for independent Python processes on one machine.

Minimum durable concepts:

```text
symbol_claim
  account_key
  symbol
  execution_id/client_order_id
  finality
  created_at/updated_at

cash_reservation
  account_key
  execution_id/client_order_id
  required_cash
  state
  created_at/updated_at
```

Use real atomic transactions (`BEGIN IMMEDIATE` or demonstrably equivalent) for claim/reservation races.

The account key must not require storing plaintext account IDs. Prefer stable hashed/bound identity derived from the existing account-binding model.

## 6. Shared MiniQMT runtime mode

Preserve current exclusive mode for backward compatibility, but add an explicit shared mode for multi-process same-QMT usage.

Suggested configuration concept:

```text
runtime_lock_mode = exclusive | shared
```

Requirements in shared mode:

- no qmt-path-wide execution-exclusive lock;
- each runtime/session remains locally protected;
- different processes acquire distinct MiniQMT session IDs;
- same account/different symbol execution can coexist;
- same account/same symbol is blocked by the durable symbol claim;
- shared cash race is blocked by the coordination store.

## 7. Session ID management

Replace one-shot random session generation with a bounded allocator/lease model for shared mode.

Requirements:

- caller-supplied exact `session_id` remains supported;
- automatic mode uses a bounded pool;
- deterministic/preferred candidate is allowed;
- collision/connect failure gets finite fallback attempts;
- no infinite random-session-file growth;
- a crashed process must not permanently strand an allocator lease;
- OS-released per-session file locks are an acceptable lightweight lease mechanism;
- no assumption that MiniQMT provides allocate/release session APIs.

## 8. Existing state machine and formal verification

Prefer not to add new `TradeState` values solely for finality if `MachineSnapshot.facts` can derive the required distinction safely.

The current fact that `RECOVERY_FAILED` leaves `unresolved_order=True` is useful: an unresolved `FAILED` can derive `QUARANTINED` rather than being treated as resolved.

Any change to states/events must update formal verification and refinement tests.

Add verification/tests for:

- unresolved FAILED cannot release symbol claim;
- UNKNOWN/CANCEL_REJECTED cannot release symbol claim;
- RESOLVED finality can release symbol claim;
- two different symbols can be active concurrently across processes;
- same symbol cannot be acquired by two processes;
- shared cash atomicity.

## 9. MiniQMT adapter/account resource interface

The current MiniQMT adapter already has asset-query capability. Preserve `BrokerPort` compatibility if possible by exposing account facts through an additive protocol such as:

```python
class AccountResourcePort(Protocol):
    def query_asset(self) -> BrokerAsset:
        ...
```

Do not force unrelated fake brokers to implement account-resource APIs unless necessary.

## 10. No-goals for v0.4

Do not add:

- async `order_stock_async` execution path;
- central strategy scheduler;
- RPC/gateway service;
- distributed coordination across machines;
- multi-order-in-one-ExecutionSession support;
- shared position reservation unless implementation evidence shows it is required for the stated single-unresolved-execution-per-symbol model;
- strategy-specific fee rules or TGrid logic.

## 11. Required acceptance scenarios

1. Ambiguous submit -> restart -> unique broker match -> submit count remains 1.
2. `SUBMITTED -> UNKNOWN -> WORKING -> FILLED`.
3. `WORKING -> cancel -> CANCELLING -> FILLED`.
4. `WORKING -> cancel rejected -> CANCEL_REJECTED -> UNKNOWN -> WORKING -> FILLED`.
5. Restart from active/cancel/unknown recovers safely.
6. Process A symbol X active; Process B same account/symbol X request -> blocked before broker call.
7. Process A symbol X active; Process B same account/symbol Y request -> both active successfully.
8. Shared cash 100k; concurrent reservations 60k + 50k -> at most one sequence can overrun; second fails closed.
9. Conservative estimator reserve > order notional when configured fee/withholding buffers require it.
10. FILLED may release symbol claim while cash reservation remains `SETTLEMENT_PENDING`.
11. Authoritative settlement reconciliation later releases the cash reservation.
12. Query None/exception/unknown raw status never creates resend permission.
13. Shared-mode two runtimes on same qmt path receive distinct session IDs and can coexist using fake trader/process tests.
14. Session collision/connect failure performs bounded fallback, never unbounded retry.
15. Existing 0.3.1 API tests continue to pass unchanged or with only import/version adjustments.

## 12. Release expectation

Target release: `0.4.0` (or a clearly documented equivalent).

Before handoff:

```text
full pytest
compileall src/tests
formal verifier
Python 3.9 compatibility
wheel build + clean install + out-of-tree verifier
Windows mutex/session-lease probes
cross-process symbol/cash concurrency tests
no real or simulation QMT order/cancel calls
```

Live execution remains fail-closed by default.