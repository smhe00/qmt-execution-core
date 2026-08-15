# Architecture

## Dependency rule

```text
project strategy
      |
      v
ExecutionSession / generic domain
      |
      v
BrokerPort
      |
      +---- MiniQmtBrokerAdapter
      +---- FakeBroker
      +---- future broker adapter
```

The generic core never imports `xtquant`.

## Generic core responsibilities

- execution lifecycle state machine;
- durable intent / cancel intent;
- cross-cycle client id and remark idempotency;
- crash-safe journal;
- cross-process execution mutex;
- query/recovery rules;
- normalized broker DTOs;
- execution evidence contract;
- common hard-limit guard wrapper;
- explicit-state verification.

## MiniQMT runtime responsibilities

- lazy import of the MiniQMT environment;
- QMT userdata path validation;
- fingerprint-only account binding;
- exact account type/status selection;
- `start -> connect -> subscribe` lifecycle;
- raw QMT status normalization;
- strict query semantics;
- immutable callback bridge;
- bounded serial callback event queue;
- disconnect invalidation;
- reconnect/account/subscription/reconcile recovery;
- live config enable + runtime-only confirmation gate.

## Project responsibilities

- signal generation;
- trading calendar/window policy;
- fresh quote evidence;
- project-specific cash/position rules;
- portfolio/core-position invariants;
- project-specific risk budgets.

## Concurrency model

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

The order-execution transaction itself is protected by an `ExecutionMutex`
which is acquired before journal load/create and held for the whole
`ExecutionSession` lifetime.

## Recovery authority

```text
Durable Journal + Broker Query
```

Callbacks are low-latency observations, not restart authority.

## Live execution authority

```text
environment == live
AND trusted config: live_trading_enabled == true
AND runtime-only confirmation token matches configured SHA-256
AND transport/account/subscription are healthy
AND durable recovery is complete
AND event queue is healthy
AND project precheck evidence passes
```

No one condition is sufficient by itself.

## Multi-strategy deployment

The library is reusable from multiple strategy projects. If several strategies
share one QMT account concurrently, the recommended next deployment layer is a
single-account execution daemon/gateway around this package rather than
allowing unrelated processes to manage the same account independently.

That service layer is intentionally outside the package's core state-machine
semantics; it can be added without changing `BrokerPort` or `ExecutionSession`.


## Cross-project process ownership

`MiniQmtRuntime` owns a QMT-path-scoped runtime mutex for its full lifetime. Project-specific `ExecutionSession` locks protect journals, while the runtime mutex prevents multiple projects from independently owning the same QMT transport/session concurrently.
