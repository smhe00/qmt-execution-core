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

The generic core must never import `xtquant`.

## What belongs in the core

- order execution lifecycle;
- durable intent and journal semantics;
- query/recovery rules;
- cancel/re-query rules;
- cross-process execution mutex;
- abstract safety facts and invariants;
- broker-neutral DTOs;
- explicit-state verification.

## What does not belong in the core

- trading signals;
- portfolio target selection;
- TGrid Core/T-Lot semantics;
- reverse-repurchase timing logic;
- ETF allocation models;
- project-specific risk budgets;
- automatic real-money enablement.

## Production roadmap

### v0.1 — reusable kernel

- generic state machine;
- MiniQMT status profile;
- dependency-injected adapter;
- fake-broker tests;
- journal/mutex/recovery;
- explicit-state verifier.

### v0.2 — audited MiniQMT session bootstrap

- validated QMT path/account binding;
- exact account type/status checks;
- callback/event-queue lifecycle;
- disconnect recovery;
- persistent account/session configuration;
- production-shaped simulation certification.

### v0.3 — execution service

- single-account daemon;
- multi-strategy request isolation;
- durable shared order registry;
- strategy quotas and execution risk guard;
- IPC/API boundary.
