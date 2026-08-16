# Agent Operating Contract

This repository is reusable execution infrastructure, not a trading strategy.

## Preserve the product boundary

- Do not add TGrid, grid, CorePosition, ETF allocation, repo timing, signal generation, target portfolio logic, or other project-specific semantics to `src/qmt_execution_core`.
- The generic core must not import `xtquant`; MiniQMT integration belongs under `miniqmt/` and is dependency injected/lazy imported.
- `ExecutionSession` remains one-active-execution-at-a-time. Do not turn it into a multi-order OMS without an explicit new specification.
- Async `order_stock_async()` is not part of the v0.4 execution path.

## Reliable execution invariants

- Durable intent is committed before any broker submit side effect.
- Durable cancel intent is committed before any broker cancel side effect.
- `UNKNOWN` / ambiguous submission may recover only through broker query/reconciliation; never auto-resubmit.
- Cancel API success means only that the request was sent; final cancellation requires authoritative re-query.
- Fill during cancel resolves to `FILLED` when broker authority reports it.
- Query `None`, exceptions, malformed status, or non-unique recovery matches fail closed.
- Broker callbacks only emit immutable observations into the bounded serial queue. They do not mutate strategy state, journal, coordination resources, or send orders.

## v0.4 shared-account invariants

- At most one unresolved execution may own `(account_key, symbol)` across processes.
- Different symbols must not be globally blocked solely because another execution is active.
- Different accounts are isolated by `account_key`, including when trading the same symbol.
- Coordinated BUY requires fresh authoritative broker available cash.
- Coordinated BUY requires an explicit conservative `CashRequirementEstimator`; never silently fall back to `qty * price` in shared/live coordination.
- Shared cash reservation uses an atomic cross-process transaction before project sidecar/broker submit.
- Submit order in coordinated mode is:

```text
Core durable intent
→ shared symbol/cash coordination
→ project before_broker_submit sidecar
→ BrokerPort.place_order
```

- `PRE_BROKER_REJECTED` is a normal local fail-closed rejection: broker was not invoked and `submitted_once` must remain false.
- `PRE_BROKER_ABORTED` records synchronous pre-broker failure: broker was not invoked; `FAILED` is therefore execution-final/resolved.
- `FAILED + unresolved_order=True` is `QUARANTINED`, not permission to release the symbol claim.
- Only `ExecutionFinality.RESOLVED` releases shared symbol/cash resources.
- Releasing a local cash reservation never credits cash locally. The next BUY must query broker available cash again.
- Do not add a local `SETTLEMENT_PENDING` cash ledger to v0.4; that design was explicitly rejected.

## Runtime concurrency invariants

- `runtime_lock_mode="exclusive"` remains the default and preserves the qmt-path-wide mutex.
- `runtime_lock_mode="shared"` must require a durable coordinator.
- Shared mode must not silently fall back to exclusive/global serialization or uncoordinated operation.
- Shared mode uses bounded MiniQMT session-id leases with finite fallback. Never introduce unbounded random session-id retries/files.
- A process crash must not permanently strand a session-id lease; OS-backed lock lifetime is the intended mechanism.

## Formal verification and source integrity

- Missing protected execution source files must fail formal verification.
- Any state/event change requires verifier and refinement-test updates.
- Formal verification does not replace runtime, fault-injection, cross-process, or Windows tests.
- Journal transition/source hashes are a safety boundary. Do not bypass a hash mismatch to make an old journal open under new execution semantics.

Before changing execution semantics run:

```bash
python -m pytest
python -m compileall -q src tests
PYTHONPATH=src python -c "from qmt_execution_core import verify_state_machine; print(verify_state_machine())"
```

For mutex/coordination/session-lease changes, Windows safety probes are mandatory.

## Production-live boundary

Real-money execution remains fail-closed by default. Never weaken:

- `live_trading_enabled` + runtime-only confirmation double gate;
- exact account binding/type/status verification;
- disconnect invalidation and reconnect reconciliation;
- event-queue health;
- project `ExecutionGuard` evidence;
- shared coordination when shared mode is selected.
