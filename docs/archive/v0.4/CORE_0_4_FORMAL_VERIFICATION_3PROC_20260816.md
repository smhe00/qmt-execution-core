# Core 0.4 Formal Verification — 3 Independent Processes

> **Date:** 2026-08-16  
> **Status:** PASS  
> **Verified branch:** `agent/v0.4-resource-coordination`  
> **Verified code head:** `1958918db4e4f7e729a62150c2f0db45b918dd39`  
> **GitHub Actions run:** `31924805322`  
> **Canonical specification:** `CORE_SPEC_V0_4_RESOURCE_COORDINATION.md`

## 1. Verification structure

The release gate deliberately separates three proofs:

1. **Single-process abstract state-machine proof** — exhaustively explores reachable `MachineSnapshot = TradeState × SafetyFacts` states through the executable `TRANSITIONS`/`advance()` implementation.
2. **Python implementation refinement proof** — statically extracts the runtime state mutation/event-emission surface and exhaustively checks every broker-observation refinement against the executable transition table.
3. **Three-process product-state proof** — composes three independent executable local state machines with the v0.4 shared symbol/cash coordination semantics and exhaustively explores all reachable interleavings for four deployment classes.

The expensive product proof is a CI/release gate and is not executed on every `ExecutionSession.open()`. Runtime session open retains the fast single-process proof plus protected-source hash binding.

## 2. Code → state-machine extraction / refinement

`src/qmt_execution_core/formal.py::verify_runtime_transition_refinement()` AST-parses the execution implementation.

It proves that the runtime `_snapshot` write surface contains exactly three sites:

```text
ExecutionSession.__init__     <- MachineSnapshot()
ExecutionSession.open         <- journal.open()
ExecutionSession._transition  <- advance()
```

Therefore, after initial construction or durable journal restore, executable lifecycle mutation cannot bypass `advance()` without making the formal gate fail.

It also extracts literal `_transition(TradeEvent.X)` emitters and the dynamic event produced by `event_for_observation()`, then exhaustively evaluates:

```text
7 broker-observation entry states × 9 BrokerOrderStatus values
```

Final refinement metrics:

```text
direct transition call sites             25
dynamic transition call sites             1
runtime state write sites                  3
observation entry states                   7
broker order statuses                      9
legal observation edges                   51
ambiguous observation pairs               11
no-op observation pairs                    1
illegal observation edges                  0
undeclared runtime events                  0
declared events without runtime emitter    0
hidden runtime state mutations             0
```

### Defect found by the refinement proof

The first formal run (`31924715047`) failed with exactly one implementation/model mismatch:

```text
CANCEL_REJECTED + broker ACCEPTED
    -> event_for_observation returned RECOVERY_ACCEPTED
    -> TRANSITIONS[CANCEL_REJECTED] did not declare RECOVERY_ACCEPTED
```

That run ended with `87 passed / 1 failed`; the three-process product proof itself passed.

The defect was fixed at:

```text
ac7a296465fb2a618fe5e3217117db6236ac07f8
```

The correction is fail-closed and does not widen the frozen state machine:

```text
CANCEL_REJECTED + ACCEPTED
    -> QUERY_AMBIGUOUS
    -> UNKNOWN
    -> later authoritative WORKING/PARTIAL/FILLED/CANCELLED recovery
```

This preserves the original execution and claim and creates no resend permission.

## 3. Single-process executable state-machine proof

Final installed-wheel verifier:

```text
declared states                    16
declared transitions               80
reachable abstract states          52
reachable transitions             211
unreachable states                  0
unreachable transitions             0
states without terminal path        0
invariant violations                0
v0.4 finality violations            0
```

Hashes at the verified head:

```text
transition_spec_sha256
  dede4b84970c59492685b5caf69ecf3f10600114751d930468c2cc2ff7ee8ebe

execution_source_sha256
  1056bc4c2a0160e9a5777150fe8d51e532542eeef7934456aa475ef299ff16fd
```

`formal.py` is part of the protected execution-source manifest, so changing the release proof implementation changes the execution-source binding.

## 4. Three-process product-state model

The product verifier uses the actual executable local machine:

```text
initial_snapshot()
TRANSITIONS
advance()
assert_invariants()
execution_finality()
```

Each process has an independent local execution lifecycle and journal/state-machine abstraction. The product adds shared-resource phase state corresponding to the production coordinator:

```text
(account_key, symbol) claim
account-scoped BUY cash reservation
```

The model includes:

- durable intent before coordination;
- atomic coordination success/rejection;
- synchronous project-sidecar abort before broker;
- broker submit accepted / ambiguous / definitively rejected;
- restart after durable intent before coordination;
- conservative quarantine/claim restore;
- UNKNOWN / CANCEL_REJECTED / FAILED-unresolved claim retention;
- RESOLVED release;
- all three processes interleaving independently.

The proof covers one logical execution lifecycle per process. Cross-cycle identity reuse/idempotency remains covered by the existing journal/single-process model and tests.

## 5. Three-process scenarios and state-space size

### Scenario A — same account, three distinct symbols

```text
P0: Account0 / Symbol0 / BUY 30
P1: Account0 / Symbol1 / BUY 30
P2: Account0 / Symbol2 / BUY 30
Fresh cash capacity: 100
```

Result:

```text
reachable global states       132,651
interleaving edges          1,537,191
invariant violations                0
all three WORKING witness         YES
```

This is the constructive proof for independent cross-symbol concurrency.

### Scenario B — same account, same symbol exclusion

```text
P0: Account0 / Symbol0 / BUY
P1: Account0 / Symbol0 / BUY
P2: Account0 / Symbol0 / SELL
```

Result:

```text
reachable global states        65,853
interleaving edges            429,021
invariant violations                0
same-symbol exclusivity violations  0
```

### Scenario C — same-account cash contention

```text
P0: BUY 60
P1: BUY 50
P2: BUY 40
Fresh cash capacity: 100
```

Result:

```text
reachable global states       102,334
interleaving edges            958,591
invariant violations                0
cash authorization violations       0
```

The invariant is stated over broker-invoked active BUYs. Conservatively restored quarantined reservations may exceed a later broker snapshot, but they never authorize a broker side effect; later `prepare()` subtracts them.

### Scenario D — cross-account same-symbol independence

```text
P0: Account0 / Symbol0 / BUY 30
P1: Account1 / Symbol0 / BUY 30
P2: Account0 / Symbol1 / BUY 30
Cash capacity: 100 per account
```

Result:

```text
reachable global states       132,651
interleaving edges          1,537,191
invariant violations                0
all three WORKING witness         YES
cross-account same-symbol witness YES
```

## 6. Aggregate product proof

```text
independent processes                         3
local reachable snapshots                    52
product scenarios                             4
total reachable global states           433,489
total interleaving edges               4,461,994
same-symbol exclusivity violations             0
shared-cash authorization violations           0
resource-release violations                    0
quarantine-claim violations                    0
cross-symbol concurrency witness             YES
cross-account same-symbol witness            YES
```

No unexpected nonterminal global deadlock was found. States with no successor are accepted only when all three local executions are terminal.

## 7. CI / packaging evidence

Final push run `31924805322` at `1958918db4e4f7e729a62150c2f0db45b918dd39`:

```text
Linux Python 3.9      PASS
Linux Python 3.11     PASS
Linux Python 3.12     PASS
Windows safety        PASS
full pytest           88 passed (Python 3.11: 38.38s)
compileall            PASS
wheel build           PASS
wheel reinstall       PASS
out-of-tree CLI proof PASS
```

The installed-wheel command:

```text
qmt-execution-core verify
```

now executes all three proof layers and reported `release_formal_verification = PASS`.

## 8. Proof boundary

This formal verification proves the executable abstract execution/coordination protocol under the modeled local-process and shared-resource semantics. It does **not** claim to prove:

- the internals of SQLite itself;
- the operating-system file-lock implementation;
- MiniQMT server correctness;
- distributed coordination across machines;
- `order_stock_async()` (not implemented in v0.4);
- fairness/scheduling guarantees;
- strategy-specific business semantics.

Those refinement boundaries are covered by concrete SQLite multiprocessing tests, Windows lock/session-lease tests, MiniQMT fake-adapter/runtime tests, and project-level tests.

All formal/CI validation used fake Broker/Fake XtQuant. No real or simulation QMT submit/cancel side effect was executed.

## 9. Formal conclusion

**PASS.**

The Core 0.4 executable local state machine, Python transition/refinement implementation, and the modeled state space of three independent strategy processes satisfy the frozen v0.4 safety invariants for symbol exclusivity, independent cross-symbol concurrency, shared cash authorization, resource release, and quarantine retention.
