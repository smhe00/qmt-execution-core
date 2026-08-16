# Changelog

## 0.4.0

- Added additive `ExecutionFinality` semantics: `OPEN`, `RESOLVED`, and `QUARANTINED`.
- Added explicit `PRE_BROKER_REJECTED` for local fail-closed rejection before broker invocation; it remains `REJECTED` without falsely setting `submitted_once`.
- Added explicit `PRE_BROKER_ABORTED` refinement so synchronous pre-broker failures are distinguishable from unresolved broker ambiguity.
- Added SQLite-backed cross-process `(account_key, symbol)` execution claims.
- Added atomic shared BUY cash reservations using fresh broker cash minus active same-account reservations.
- Added broker-neutral `CashRequirementEstimator` / `CashRequirementEstimate` with configurable fee, temporary withholding, FX/rounding, and safety buffers.
- Added `AccountResourcePort` without widening the existing `BrokerPort` contract.
- Added `CoordinatedExecutionSession` while retaining one-active-execution-per-session semantics.
- Submit ordering in coordinated mode is now: durable intent → shared coordination → project sidecar → broker side effect.
- Added shared MiniQMT runtime mode. Existing `exclusive` mode remains the default for backward compatibility.
- Added bounded, OS-lock-backed MiniQMT session-id leases with finite connect fallback.
- Added multi-account coordination keys and same-symbol isolation by account.
- Added cross-process same-symbol, cross-symbol, shared-cash, quarantine, session-id, and sidecar-ordering tests.
- Added Windows CI safety probes for msvcrt mutex/session leases and SQLite coordination.
- No settlement-pending cash ledger is introduced: terminal/resolved execution releases the local reservation, and every later BUY must refresh authoritative broker available cash.

## 0.3.1

- Relaxed supported Python version to `>=3.9` after Windows Python 3.9 validation.
- Added Python 3.9/3.11/3.12 CI coverage.

## 0.3.0

- Added broker-neutral synchronous `before_broker_submit` and `before_broker_cancel` lifecycle sidecar hooks.
- Hooks run after core durable intent/cancel-intent persistence and before broker side effects.

## 0.2.1

- Fixed Windows `ExecutionMutex` byte-0 unlock behavior across repeated owners.
- Added cancel-rejected/re-query and restart-cancel-pending regressions.

## 0.2.0

- Added production-shaped `MiniQmtRuntime`.
- Added fingerprint-only account/QMT path binding.
- Added exact account type/status selection and reconnect verification.
- Added bounded serial callback event queue and fail-closed health propagation.
- Added live double gate: trusted config enable + runtime-only confirmation token.
- Added disconnect recovery: reconnect → account verify → subscribe → reconcile → reconfirm.
- Added MiniQMT asset/position/trade DTO queries.
- Added cross-cycle durable idempotency for client order ids and order remarks.
- Added common allowlist/qty/notional guard wrapper and kill switch.
- Added CLI for formal verification, account binding creation and token hashing.

## 0.1.0

- Initial generic execution state machine, journal, mutex, recovery, MiniQMT status normalization and explicit-state verifier.
