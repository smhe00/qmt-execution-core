# qmt-execution-core 0.4 Implementation Evidence

> **Status：REVIEW READY**  
> **Target release：0.4.0**  
> **Implementation branch：`agent/v0.4-resource-coordination`**  
> **Frozen specification：[`CORE_SPEC_V0_4_RESOURCE_COORDINATION.md`](CORE_SPEC_V0_4_RESOURCE_COORDINATION.md)**  
> **0.3.1 implementation baseline：`937e6a4a1cbd54df960f9bde3ca2e91d6bc19c79`**  
> **v0.4 work started from main：`79ccaa562e16969f96994e60799f23fb72f1a8b7`**

本文记录冻结 v0.4 规格到实现、测试和 CI 的可审计映射。它不是新的需求来源；如与冻结规格冲突，以冻结规格为准。

---

## 1. Implementation Summary

0.4.0 已实现：

- `ExecutionFinality`: `OPEN / RESOLVED / QUARANTINED`；
- `PRE_BROKER_REJECTED`：本地 fail-closed rejection，不伪造 `submitted_once`；
- `PRE_BROKER_ABORTED`：同步 pre-broker failure，明确证明 broker 未调用；
- durable cross-process `(account_key, symbol)` claim；
- SQLite atomic shared BUY cash reservation；
- fresh authoritative broker cash verification；
- broker-neutral conservative `CashRequirementEstimator`；
- multi-account `account_key` isolation；
- additive `AccountResourcePort`；
- additive `CoordinatedExecutionSession`；
- MiniQMT `exclusive | shared` runtime modes；
- bounded OS-lock-backed MiniQMT session-id leases；
- finite session collision/connect fallback；
- Linux Python 3.9/3.11/3.12 full CI；
- Windows mutex / SQLite / session lease safety CI；
- wheel build/reinstall/out-of-tree verifier。

明确没有实现：

- settlement-pending local cash ledger；
- async `order_stock_async()` execution path；
- central scheduler / RPC gateway / distributed OMS；
- one-session multi-order engine；
- shared position coordinator。

---

## 2. Frozen Spec → Code Mapping

| Frozen Requirement | Implementation | Primary Tests |
|---|---|---|
| Same `(account_key,symbol)` unresolved execution exclusivity | `coordination.py::SQLiteExecutionCoordinator` | `test_coordination.py`, `test_shared_runtime.py` |
| Different symbols can execute concurrently | shared runtime + independent sessions | `test_shared_runtime.py` |
| Different accounts isolate same symbol | hashed `account_key` | `test_coordination.py`, `test_shared_runtime.py` |
| Durable intent before coordination/broker | `ExecutionSession.submit()` | existing session tests + `test_coordination_sidecar_order.py` |
| Coordination before project sidecar | `before_submit_coordination` seam | `test_coordination_sidecar_order.py` |
| Project sidecar before broker side effect | existing hook preserved | existing hook tests + ordering test |
| Local coordination reject before broker | `PRE_BROKER_REJECTED` | `test_prebroker_reject.py`, shared-runtime tests |
| Proven no-submit synchronous failure | `PRE_BROKER_ABORTED` | existing hook tests + sidecar-ordering test |
| UNKNOWN never blind-resends | existing state/recovery + verifier | existing recovery tests |
| Unresolved FAILED retains symbol | `ExecutionFinality.QUARANTINED` | `test_coordinated_session.py` |
| Only RESOLVED releases shared resources | `update_finality()` | coordination/coordinated-session tests |
| Atomic shared BUY cash | SQLite `BEGIN IMMEDIATE` | multiprocessing cash-race test |
| Fresh broker cash for each BUY | `CoordinatedExecutionSession._coordinate_before_submit()` | shared runtime tests |
| Conservative fee/withholding/FX buffer | `CashRequirementEstimator` | `test_coordination.py` |
| Released reservation is not local cash credit | no local cash increment; next BUY receives new broker snapshot | `test_coordination.py` |
| No Settlement Pending ledger | reservation only active/released | schema/tests/docs |
| Existing BrokerPort remains narrow | additive `AccountResourcePort` | existing BrokerPort tests remain green |
| ExecutionSession remains single-active | existing lifecycle unchanged | existing session tests |
| Shared qmt path multi-runtime | `runtime_lock_mode="shared"` | `test_shared_runtime.py` |
| Default backward-compatible global mutex | `runtime_lock_mode="exclusive"` default | existing MiniQMT runtime mutex tests |
| Bounded session IDs | `miniqmt/session_id.py` | shared runtime tests + Windows safety |
| Crash-safe session lease semantics | OS file lock lifetime | mutex/session lease tests |
| Formal finality refinement | `verifier.py` | installed-wheel verifier |

---

## 3. Shared Coordination Schema

SQLite schema v1 contains only cross-process shared account resources:

```text
symbol_claim
  PRIMARY KEY(account_key, symbol)
  execution_id
  client_order_id
  finality

cash_reservation
  PRIMARY KEY(account_key, execution_id, client_order_id)
  symbol
  required_cash
  active
```

Critical read-check-write operations use `BEGIN IMMEDIATE`.

The DB is deliberately **not** a strategy business database and does not contain TGrid/ETF strategy state.

---

## 4. Submit Ordering Evidence

Coordinated mode executes:

```text
ExecutionGuard / PRE_CHECK
        |
        v
Core Durable Intent COMMIT
        |
        v
Shared Coordination
  - symbol claim
  - fresh account cash
  - conservative reservation
        |
        v
Project before_broker_submit sidecar
        |
        v
BrokerPort.place_order()
```

`tests/test_coordination_sidecar_order.py` proves that at project-sidecar time:

- shared symbol claim already exists；
- BUY reservation already exists；
- broker `place_calls == 0`。

If the project sidecar raises synchronously:

- broker remains uncalled；
- shared claim/reservation are released；
- state becomes `FAILED` through `PRE_BROKER_ABORTED`；
- `unresolved_order=False`；
- `ExecutionFinality=RESOLVED`；
- the original exception propagates。

---

## 5. Pre-broker Rejection Semantics

0.4 distinguishes two cases that 0.3 did not model explicitly.

### Normal local fail-closed reject

Example: same-symbol claim conflict / insufficient shared cash.

```text
SUBMITTED
 -> PRE_BROKER_REJECTED
 -> REJECTED
```

Facts:

```text
broker invoked = false
submitted_once = false
unresolved_order = false
```

### Synchronous infrastructure abort

Example: project durable business sidecar fails.

```text
SUBMITTED
 -> PRE_BROKER_ABORTED
 -> FAILED
```

Facts:

```text
broker invoked = false
submitted_once = false
unresolved_order = false
ExecutionFinality = RESOLVED
```

This remains distinct from:

```text
UNKNOWN -> recovery failure -> FAILED
unresolved_order = true
ExecutionFinality = QUARANTINED
```

---

## 6. CI Evidence

### Code-validation run

GitHub Actions run:

```text
31924076451
head = e9469a34826912663e4ce1c3d381840002c4840d
conclusion = success
```

All four jobs passed:

```text
Linux Python 3.9   PASS
Linux Python 3.11  PASS
Linux Python 3.12  PASS
Windows safety     PASS
```

### Full suite

Python 3.11 log:

```text
86 passed in 1.73s
```

Linux matrix also completed successfully on Python 3.9 and 3.12.

### Windows safety

Windows Server 2025 / Python 3.11:

```text
20 passed in 5.43s
```

Dedicated Windows set covers:

- repeated msvcrt execution mutex ownership；
- SQLite cross-process symbol claim；
- SQLite cross-process cash race；
- coordinated UNKNOWN/quarantine；
- same-qmt-path shared runtimes；
- bounded session-id lease/conflict/release。

### Build/install

CI also passed:

```text
python -m compileall -q src tests
python -m pip wheel --no-deps . -w dist
python -m pip install --force-reinstall dist/qmt_execution_core-*.whl
cd /tmp && qmt-execution-core verify
```

Built artifact:

```text
qmt_execution_core-0.4.0-py3-none-any.whl
```

---

## 7. Formal Verifier Evidence

Installed-wheel verifier at code-validation commit:

```text
declared_states                    = 16
declared_transitions               = 80
reachable_abstract_states          = 52
reachable_transitions              = 211
unreachable_states                 = 0
unreachable_transitions            = 0
states_without_terminal_path       = 0
invariant_violations               = 0
v0_4_finality_invariant_violations = 0
```

Hashes:

```text
transition_spec_sha256
= dede4b84970c59492685b5caf69ecf3f10600114751d930468c2cc2ff7ee8ebe

execution_source_sha256
= 7c2fda6bad41c5dff371de939fa6881cdcf113aebe8016f136004b1e4897c6c0
```

Protected sources include the new finality, coordination, coordinated-session and MiniQMT session-id modules.

---

## 8. API Compatibility

Existing strategy-facing shapes remain available:

```text
ExecutionRequest
ExecutionSnapshot
BrokerPort
ExecutionGuard
ExecutionSession.submit/poll/cancel/reconcile/next_cycle
MiniQmtRuntime.submit/poll/cancel/next_cycle
before_broker_submit
before_broker_cancel
```

0.4 additions are additive:

```text
AccountResourcePort
ExecutionFinality
ExecutionCoordinator
SQLiteExecutionCoordinator
CashRequirementEstimator
CashRequirementEstimate
CoordinatedExecutionSession
runtime_lock_mode
coordination_path
bounded session-id configuration
```

Default runtime mode remains `exclusive`.

---

## 9. Cash Model Note

The frozen specification deliberately chooses a conservative formula:

```text
effective cash
= fresh broker available cash
- active local cross-process reservations
```

If a broker has already reflected a working order's frozen amount in its own `available_cash` while the corresponding local reservation remains active, this may temporarily **under-utilize** cash by double-conservatism.

This is a utilization tradeoff, not a safety failure. v0.4 prioritizes prevention of cross-process overcommit. No optimization that risks double-spending shared cash was added outside the frozen specification.

---

## 10. 0.3.1 Journal Upgrade Boundary

Execution journals bind both:

```text
transition_spec_sha256
execution_source_sha256
```

0.4 changes both protected source and transition specification. An old 0.3.1 journal must therefore **not** be silently opened by bypassing hash verification.

Migration sequence:

1. use the old runtime/broker authoritative query to confirm the prior execution is resolved；
2. ensure no `UNKNOWN / WORKING / CANCELLING / PARTIALLY_FILLED` execution remains；
3. archive the old journal for audit；
4. use a new 0.4 journal path；
5. then enable coordinated/shared runtime mode。

Never delete/bypass a journal merely to get past the hash gate while an active broker order may still exist.

---

## 11. Real/Simulation QMT Side-effect Boundary

All CI validation uses fake BrokerPort / fake XtQuant dependencies.

```text
real QMT order calls:       0
simulation QMT order calls: 0
```

No live or MiniQMT simulation order/cancel was authorized or executed as part of v0.4 implementation.

---

## 12. Review Conclusion

Implementation status:

```text
FROZEN SPEC IMPLEMENTED
CODE VALIDATION PASS
WINDOWS SAFETY PASS
API COMPATIBILITY PRESERVED ADDITIVELY
REAL/SIMULATION QMT SIDE EFFECTS = 0
READY FOR INDEPENDENT REVIEW
```

Next integration target after Core review/merge: TGrid should pin the reviewed 0.4 commit and migrate its project-specific reservation/business-ledger adapter to the new coordination ordering rather than reimplementing generic shared-account coordination.
