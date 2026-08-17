# Core 0.4 Independent Architecture / Code Audit

> **Date:** 2026-08-16  
> **Verdict:** **PASS**  
> **PR:** #3 — `feat: qmt-execution-core 0.4 resource coordination`  
> **Audited code head:** `1958918db4e4f7e729a62150c2f0db45b918dd39`  
> **Formal evidence:** `CORE_0_4_FORMAL_VERIFICATION_3PROC_20260816.md`  
> **Frozen spec:** `CORE_SPEC_V0_4_RESOURCE_COORDINATION.md`

This audit is intentionally separate from the formal proof and CI status. Passing tests/formal verification is supporting evidence, not the audit verdict by itself.

## 1. Audit scope

The audit reviewed the v0.4 delta and final implementation across:

- executable `TradeState × SafetyFacts` lifecycle;
- runtime event/refinement mapping;
- `ExecutionFinality` semantics;
- submit/cancel durable ordering;
- SQLite `(account_key, symbol)` execution claims;
- account-scoped shared BUY cash reservations;
- crash/restart/UNKNOWN/quarantine behavior;
- `CoordinatedExecutionSession` composition;
- MiniQMT shared/exclusive runtime modes;
- bounded session-id leases;
- callback/live/simulation safety boundaries;
- source/API compatibility with v0.3.1;
- Python 3.9 and Windows behavior;
- frozen-v0.4 non-goals and repository boundary hygiene;
- 3-process formal model and its refinement boundary.

## 2. P0 / P1 findings

**No unresolved P0 or P1 finding remains.**

One actual transition/refinement defect was found during the independent verification work and fixed before this audit was closed:

```text
CANCEL_REJECTED + broker ACCEPTED
  previously -> RECOVERY_ACCEPTED (illegal from CANCEL_REJECTED)
```

The final implementation is fail-closed:

```text
CANCEL_REJECTED + broker ACCEPTED
  -> QUERY_AMBIGUOUS
  -> UNKNOWN
  -> authoritative recovery
```

The release refinement verifier now reports zero illegal observation edges.

## 3. State-machine / implementation consistency — PASS

The runtime mutation audit proves the state surface is constrained to:

```text
__init__    -> initial MachineSnapshot
open        -> durable journal restore
_transition -> advance()
```

All runtime lifecycle event sources are checked against `TRANSITIONS`; the sole dynamic event path is `_apply_observation()` through `event_for_observation()`.

Final implementation-refinement result:

```text
runtime state writes                        3
illegal observation edges                  0
undeclared runtime events                  0
declared events without runtime emitter    0
hidden state mutations                     0
```

Audit conclusion: executable transition authority is singular and mechanically checked.

## 4. Durable ordering — PASS

Final submit ordering is:

```text
ExecutionGuard / PRE_CHECK
-> core durable intent
-> shared symbol/cash coordination COMMIT
-> project before_broker_submit sidecar
-> BrokerPort.place_order
```

This satisfies the frozen safety ordering:

- broker side effect cannot precede core durable identity;
- shared resources are acquired before a second account writer can submit;
- project business sidecar still runs before broker submit;
- synchronous pre-broker rejection/abort records proven-no-submit semantics;
- ambiguous broker outcomes retain claim/reservation.

Cancel ordering retains the v0.3 durable cancel-intent-before-side-effect rule and broker re-query authority.

## 5. Execution finality / quarantine — PASS

The additive finality model is correctly derived from `TradeState + SafetyFacts`:

- active/recoverable states -> `OPEN`;
- confirmed terminal/proven-never-submitted -> `RESOLVED`;
- `FAILED + unresolved_order` -> `QUARANTINED`.

`UNKNOWN`, `CANCEL_REJECTED`, and unresolved `FAILED` retain symbol ownership. Only `RESOLVED` releases resources.

This prevents a local error label from becoming implicit resend permission.

## 6. SQLite symbol coordination — PASS

`SQLiteExecutionCoordinator` uses:

```text
BEGIN IMMEDIATE
PRIMARY KEY(account_key, symbol)
```

for atomic read-check-write ownership.

The claim is account-scoped and therefore simultaneously supports:

- same-account same-symbol exclusion;
- same-account different-symbol concurrency;
- cross-account same-symbol concurrency.

Concrete multiprocessing tests verify real SQLite process contention in addition to the abstract product model.

## 7. Shared cash — PASS

BUY preparation obtains a fresh authoritative account asset snapshot before entering the serialized coordinator transaction.

Within that transaction:

```text
effective_cash
= fresh_broker_available_cash
- active_same_account_local_reservations
```

and the conservative estimator can add transaction cost, temporary withholding, FX/rounding, and safety buffers.

Important safety properties are preserved:

- reservation happens before broker submit;
- concurrent writers cannot both consume the same unreserved cash under a shared coordinator;
- `UNKNOWN` / quarantine retains reservation;
- release does not locally credit cash;
- every later BUY gets a new broker cash snapshot.

The model can be conservative if broker available cash already reflects some broker-side freezes; that reduces utilization but does not create overspending authority.

## 8. Dual durable stores / failure windows — PASS with conservative P2 note

The execution journal and SQLite coordinator are two durability domains, not one distributed transaction. Ordering is designed so that crash/error windows fail closed.

Examples:

- crash after durable intent but before broker submit can become a conservative quarantine;
- coordinator release failure after definitive broker rejection can leave a stale claim/reservation;
- sidecar/release storage failures can produce false quarantine or require operator reconciliation.

These are availability/operability degradations, not blind-resend or double-submit paths. The implementation deliberately prefers stale ownership over unsafe release.

**P2 operational note:** future versions may add explicit stale-resource reconciliation tooling, but v0.4 is safe without it.

## 9. Coordination-database deployment invariant — PASS with explicit precondition

All independent writers for the same broker account must use the same durable coordination domain (normally one account-level SQLite DB, or a deliberately shared multi-account DB).

No local library can detect another process that was intentionally pointed at an unrelated database path. Therefore:

> the coordination DB path/configuration is a safety-critical deployment invariant.

Deleting, splitting, or independently recreating the coordination DB while unresolved orders exist is outside the supported fault model and must not be treated as automatic recovery.

This is consistent with the frozen v0.4 deployment model. TGrid integration must centralize this configuration rather than allow each strategy to invent a path.

## 10. Restart / UNKNOWN / claim restore — PASS

For ordinary process crash, the SQLite claim survives independently of the strategy process. Restart recovery therefore encounters the already-durable ownership record.

If an unresolved local journal sees a missing claim, restore is conservative and never grants a new broker submit; conflicting ownership fails closed.

The audit does not treat physical loss/deletion of the coordination database as a supported crash-recovery event; see the deployment invariant above.

## 11. MiniQMT shared runtime / session IDs — PASS

Shared mode:

- removes the qmt-path-wide runtime exclusion;
- retains independent local `ExecutionSession` journal locks;
- requires a durable coordinator;
- leases a bounded per-qmt-path MiniQMT session id using an OS file lock;
- has finite deterministic candidate fallback;
- releases the lease on close/error; process death relies on OS lock release.

Caller-specified exact session id remains supported and exclusive within the qmt-path lease domain.

`exclusive` mode remains the default, preserving v0.3 behavior.

## 12. Three-process concurrency proof — PASS

The release formal gate exhaustively explores four 3-process resource-equivalence scenarios:

```text
433,489 reachable global states
4,461,994 interleaving edges
0 same-symbol exclusivity violations
0 shared-cash authorization violations
0 resource-release violations
0 quarantine-claim violations
```

Constructive witnesses exist for:

- all three independent same-account/different-symbol executions simultaneously `WORKING`;
- cross-account same-symbol executions simultaneously `WORKING`.

The audit accepts the proof boundary: SQLite/OS/MiniQMT internals are not mathematically modeled; concrete multiprocessing/Windows/fake-runtime refinement tests cover those implementation boundaries.

## 13. Live / simulation / callback safety — PASS

v0.4 does not weaken:

- fingerprint account binding;
- live double gate;
- disconnect invalidation;
- reconnect/reconciliation requirement;
- event-queue health gating;
- callback isolation.

Callbacks remain observations only; they do not acquire shared cash, submit/cancel, or own restart authority.

## 14. API compatibility — PASS

Existing strategy-facing surfaces remain available:

- `ExecutionRequest`;
- `ExecutionSnapshot`;
- `BrokerPort`;
- `ExecutionGuard`;
- `ExecutionSession.submit/poll/cancel/reconcile/next_cycle`;
- `MiniQmtRuntime.submit/poll/cancel/next_cycle`;
- `before_broker_submit` / `before_broker_cancel`.

New resource functionality is additive (`AccountResourcePort`, coordinator/finality/shared runtime configuration).

`ExecutionSession` remains one-active-execution-at-a-time; v0.4 does not turn it into a multi-order engine.

## 15. Repository boundary / non-goals — PASS

Final repository scan found no implementation of:

```text
SETTLEMENT_PENDING
order_stock_async
TGrid-specific production logic
```

The generic core does not absorb strategy allocation/grid/portfolio semantics.

## 16. Build / platform evidence — PASS

Final code-validation run `31924805322`:

```text
Python 3.9     PASS
Python 3.11    PASS
Python 3.12    PASS
Windows safety PASS
pytest         88 passed
compileall     PASS
wheel build    PASS
wheel reinstall PASS
installed release formal verifier PASS
```

All validation uses Fake Broker/Fake XtQuant; no real or simulation QMT order/cancel side effect was executed.

## 17. Release risks / non-blocking follow-up

### P2-A — stale coordination cleanup

Add explicit operator tooling later for inspecting/resolving stale `QUARANTINED` claims and reservations after storage/infrastructure faults.

### P2-B — coordination-path configuration

Projects should expose one canonical account-level coordination path and make per-strategy divergence difficult or impossible.

### P2-C — proof scope

The 3-process product proof covers one logical execution lifecycle per process and canonical resource-equivalence classes. Cross-cycle durable identity/idempotency remains verified by the single-process/journal model and tests. No fairness or distributed-machine theorem is claimed.

None of these notes authorizes weakening fail-closed behavior.

## 18. Independent audit verdict

**PASS FOR MERGE.**

Core 0.4 satisfies the frozen architecture and safety model. The formal/refinement work found and corrected one real state-transition mismatch before release. No remaining P0/P1 issue was found that should block merging PR #3 into `main`.
