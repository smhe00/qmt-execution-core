# qmt-execution-core

Reusable, fail-closed trading execution infrastructure for Python strategies,
with a broker-neutral execution kernel and a production-shaped MiniQMT/XtQuant
runtime.

The package is **strategy agnostic**:

```text
Strategy decides: why / when / how much to trade
Execution Core decides: whether execution is safe, how order state evolves,
                        and how uncertainty/restart/disconnect is recovered
```

## Architecture

```text
TGrid / Reverse Repo / ETF / Rebalance / Future Strategy
                         |
                         | ExecutionRequest
                         v
+--------------------------------------------------+
| Generic Execution Core                           |
| explicit state machine                           |
| durable intent + cross-cycle idempotency         |
| crash-safe journal                               |
| cross-process execution mutex                    |
| query-based recovery / reconciliation            |
| formal explicit-state verifier                   |
+--------------------------+-----------------------+
                           | BrokerPort
                           v
+--------------------------------------------------+
| MiniQMT Runtime                                  |
| fingerprint-bound account selection              |
| XtQuant status normalization                     |
| callback -> bounded serial EventQueue             |
| exact account health / disconnect recovery       |
| live config gate + runtime confirmation token    |
+--------------------------+-----------------------+
                           v
                        MiniQMT
```

## Safety rules

- durable intent/reservation before broker submit;
- durable cancel intent before broker cancel;
- `UNKNOWN` never auto-resubmits;
- query `None` is ambiguous, not silently empty;
- cancel API success is not terminal cancellation;
- partial fills are preserved;
- restart restores/reconciles instead of resetting;
- execution mutex is acquired before journal I/O;
- client order ids and order remarks cannot be reused across durable cycles;
- callbacks only emit immutable observations;
- unknown MiniQMT status values map to `UNKNOWN`;
- a disconnect invalidates execution immediately;
- transport reconnect alone does not restore new-order capability;
- live execution requires config enable **and** a runtime-only token;
- formal-model PASS does not replace runtime refinement tests.

## Installation

```bash
python -m pip install -e .
```

Development:

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m compileall -q src tests
qmt-execution-core verify
```

`xtquant` is intentionally **not** a PyPI dependency. It is supplied by the
MiniQMT environment at runtime. The generic package and CI therefore remain
testable without MiniQMT installed.

## MiniQMT order status mapping

| QMT | Value | Normalized |
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
| unrecognized | other | UNKNOWN |

## Package layout

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

## Generic usage

Projects can use `ExecutionSession` with any `BrokerPort`:

```python
session = ExecutionSession(
    broker=my_broker,
    guard=my_project_guard,
    journal_path="runtime/order.json",
    lock_path="runtime/order.lock",
    execution_id="strategy-a",
)
session.open()
```

## MiniQMT production-shaped usage

Create a fingerprint-only binding locally:

```bash
qmt-execution-core create-binding \
  --environment simulation \
  --account-type 2 \
  --qmt-path "C:/.../userdata_mini" \
  --output config/account-binding.local.json
```

Then:

```python
from qmt_execution_core.miniqmt import MiniQmtRuntime, MiniQmtRuntimeConfig

config = MiniQmtRuntimeConfig.from_json("config/runtime.local.json")
runtime = MiniQmtRuntime.connect(config, guard=my_project_guard)

try:
    snapshot = runtime.submit(request)
finally:
    runtime.close()
```

For live mode, `live_trading_enabled=true` is still insufficient:

```python
runtime.confirm_live("runtime-only-token")
```

The plaintext token is never persisted by the package, and confirmation is
revoked after disconnect/teardown.

## What remains project-specific

This repository does **not** contain:

- signal generation;
- grid/T-Lot/CorePosition semantics;
- ETF allocation;
- reverse-repurchase timing;
- portfolio target selection;
- project-specific cash/position/quote rules.

Projects provide `ExecutionGuard` evidence and may compose it with the common
`LimitExecutionGuard`.

## Verification

The explicit-state verifier proves the declared abstract state machine has:

- no unreachable state/transition;
- no reachable invariant violation;
- a terminal path from every reachable non-terminal state;
- no blind retry path from `UNKNOWN`.

The journal also binds itself to the state-machine transition hash and all
protected execution source files.

See:

- **[Frozen v0.4 formal specification](docs/CORE_SPEC_V0_4_RESOURCE_COORDINATION.md)**
- [Architecture](docs/ARCHITECTURE.md)
- [State-machine specification](docs/STATE_MACHINE_SPEC.md)
- [MiniQMT profile](docs/MINIQMT_PROFILE.md)
- [Production MiniQMT runtime](docs/PRODUCTION_RUNTIME.md)
- [Changelog](CHANGELOG.md)
