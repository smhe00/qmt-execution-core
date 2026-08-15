# qmt-execution-core

A reusable, fail-closed trading execution kernel with a generic order-execution state machine and a MiniQMT/XtQuant adapter profile.

The package is intentionally **strategy agnostic**. A strategy decides *why/when/how much* to trade; this package decides *whether an execution is safe to attempt, how the broker lifecycle is represented, and how uncertain states are recovered*.

## Architecture

```text
Strategy / Scheduler
        |
        | ExecutionRequest
        v
+-----------------------------+
| Generic Execution Core      |
| state machine               |
| durable journal             |
| execution mutex             |
| recovery / reconciliation   |
| formal verifier             |
+--------------+--------------+
               | BrokerPort
               v
+-----------------------------+
| MiniQMT Adapter             |
| XtQuant status normalization|
| strict query semantics      |
| callback event isolation    |
+--------------+--------------+
               v
            MiniQMT
```

## Safety model

The core follows these rules:

- durable intent before broker side effects;
- `UNKNOWN` / ambiguous broker state never auto-retries;
- cancel acknowledgement is not treated as proof of cancellation;
- broker query is the authority for recovery;
- unknown MiniQMT status values map to `UNKNOWN`, never to `WORKING`;
- one execution session can be protected by a cross-process mutex;
- journal writes are atomic (`temp -> fsync -> replace`);
- the abstract state machine is exhaustively checked by an explicit-state verifier;
- formal-model PASS does **not** replace runtime refinement tests.

## MiniQMT status mapping

| QMT status | Value | Normalized state |
|---|---:|---|
| UNREPORTED | 48 | ACCEPTED |
| WAIT_REPORTING | 49 | ACCEPTED |
| REPORTED | 50 | WORKING |
| REPORTED_CANCEL | 51 | CANCELLING |
| PARTSUCC_CANCEL | 52 | CANCELLING + partial fill |
| PART_CANCEL | 53 | CANCELLED + preserve fill |
| CANCELED | 54 | CANCELLED |
| PART_SUCC | 55 | PARTIALLY_FILLED |
| SUCCEEDED | 56 | FILLED |
| JUNK | 57 | REJECTED |
| UNKNOWN | 255 | UNKNOWN |

## Package layers

```text
src/qmt_execution_core/
├── domain.py          # generic DTOs / states / events
├── state_machine.py   # transition system + invariants
├── verifier.py        # explicit-state verification
├── journal.py         # crash-safe durable journal
├── mutex.py           # cross-process execution mutex
├── ports.py           # BrokerPort protocol
├── recovery.py        # strict broker reconciliation helpers
├── session.py         # reusable one-execution-at-a-time session API
└── miniqmt/
    ├── status.py      # raw QMT -> normalized status
    ├── adapter.py     # XtQuantTrader adapter (dependency injected)
    └── callbacks.py   # callback -> immutable event queue bridge
```

## Quick example with a fake/custom broker

```python
from pathlib import Path
from qmt_execution_core import ExecutionRequest, ExecutionSession

session = ExecutionSession(
    broker=my_broker,
    guard=my_guard,
    journal_path=Path("runtime/order.json"),
    lock_path=Path("runtime/order.lock"),
)

session.open()
snapshot = session.submit(
    ExecutionRequest(
        client_order_id="strategy-20260818-001",
        symbol="510300.SH",
        side="BUY",
        qty=100,
        limit_price=4.72,
        strategy_id="demo",
        order_remark="demo_20260818_001",
    )
)
```

## Production boundary

`v0.1` does **not** provide a production live-session bootstrap, account-binding policy, or a convenience switch that enables real-money trading. Those should be added as a separately reviewed layer after the reusable kernel is stable.

See [docs/STATE_MACHINE_SPEC.md](docs/STATE_MACHINE_SPEC.md) and [docs/MINIQMT_PROFILE.md](docs/MINIQMT_PROFILE.md).
