# qmt-execution-core 0.4.1 — Account Runtime Authority Implementation Evidence

> Date: 2026-08-16
> Branch: `feature/0.4.1-runtime-authority`
> Baseline: 0.4.0 `acf20d9fe5cf2aede3cc0ad0e8936ecb0c5b2692`
> Frozen delta spec: `docs/CORE_0_4_1_RUNTIME_AUTHORITY_SPEC.md`
> Author: DSH (implementation). All evidence SELF_CERTIFIED until independent architect audit.
> No real or simulation QMT order/cancel invoked.

## 1. Scope

Closed the Core 0.4.0 split-brain configuration hole: for one authoritative
account, shared execution now resolves through ONE canonical Account Runtime
Authority that certifies exactly ONE dedicated coordination DB instance by
canonical path + persistent DB UUID. Strategy code can no longer pick an
arbitrary `coordination_path` as proof of uniqueness.

## 2. Implementation

New/updated files (all in the protected-source manifest where applicable):

```text
src/qmt_execution_core/authority.py          NEW — AccountAuthority model,
                                             AccountRuntimeAuthority store,
                                             default_authority_root()
src/qmt_execution_core/coordination.py       CoordinationDbIdentity +
                                             SQLiteExecutionCoordinator
                                             expected_identity verification +
                                             create() authorized bootstrap
src/qmt_execution_core/exceptions.py         RuntimeAuthorityError,
                                             CoordinationIdentityError
src/qmt_execution_core/miniqmt/runtime.py    authority_root config field;
                                             shared mode resolves the Account
                                             Runtime Authority (authority
                                             first, legacy explicit path
                                             retained as documented
                                             non-uniqueness-guaranteed mode)
src/qmt_execution_core/verifier.py           PROTECTED_EXECUTION_SOURCES +=
                                             authority.py
src/qmt_execution_core/__init__.py           public exports
pyproject.toml                               version 0.4.1
tests/test_authority.py                      NEW — in-process acceptance matrix
tests/test_authority_cross_process.py        NEW — OS-process bootstrap/lock
tests/test_shared_runtime.py                 shared-mode semantics updated
                                             (authority default; corrupt
                                             authority fail-closed)
```

### Authority model (spec §4)

`AccountAuthority`: `schema_version`, `authority_id` (UUID), `account_key`,
`environment`, `account_type`, `account_id_sha256`, `coordination_db_path`
(canonical absolute), `coordination_db_uuid` (persistent instance UUID — NOT
a content hash; stable across claims/reservations/VACUUM/WAL).

Authority filename and lock are derived from `account_key`:
`<canonical-authority-root>/<account_key>.authority.json` +
`<account_key>.authority.lock`. `default_authority_root()` is host/user-level
(`%LOCALAPPDATA%\qmt-execution-core\authority` on Windows, XDG-style
otherwise); tests inject an explicit root (isolated test-only injection).

### DB identity binding (spec §5, INV-AUTH-002)

New persistent table `coordination_identity(account_key, db_uuid,
authority_id, identity_schema_version)` in the coordination DB. Shared
execution requires all of:

```text
runtime account_key == authority.account_key == DB metadata.account_key
canonical(opened DB path) == authority.coordination_db_path
authority.coordination_db_uuid == DB metadata.db_uuid
authority.authority_id == DB metadata.authority_id
```

Any mismatch raises `CoordinationIdentityError` / `RuntimeAuthorityError`
(fail closed). `expected_identity=None` keeps the 0.4.0 legacy explicit-path
mode (documented as carrying no uniqueness guarantee); legacy 0.4.0 DBs
without identity metadata are never silently adopted.

### Atomic bootstrap (spec §7, P1-3)

`AccountRuntimeAuthority.resolve(...)` acquires the per-account OS-backed
authority lock (`ExecutionMutex`, cross-process msvcrt/fcntl), then:

```text
authority exists?  -> verify only; NEVER silently rewrite
missing + bootstrap=False -> RuntimeAuthorityError (fail closed)
missing + bootstrap=True  -> create dedicated DB via
                             SQLiteExecutionCoordinator.create (refuses to
                             create over an existing file), generate
                             authority_id + db_uuid, persist DB identity,
                             atomic temp-write + fsync + replace authority
```

Two racing processes converge on one `authority_id` / `db_uuid` / domain
(proven cross-process).

### Runtime resolution (P1-4)

`MiniQmtRuntime.connect` shared mode:

```text
binding -> account_key -> canonical Authority -> verify ->
SQLiteExecutionCoordinator(certified path, expected_identity=...) ->
CoordinatedExecutionSession
```

`MiniQmtRuntimeConfig.coordination_path` and `authority_root` are mutually
exclusive; production shared mode defaults to the Authority. No broker side
effect can occur before Authority + DB identity verification succeeds (the
coordinator is constructed before the session/trader try-block).

## 3. Acceptance matrix (spec §11)

| # | Scenario | Result |
| --- | --- | --- |
| 1 | same account -> same Authority file | PASS |
| 2 | same account -> same certified DB path + UUID | PASS |
| 3 | different accounts -> different Authority + DB | PASS |
| 4 | Authority account_key / identity mismatch | PASS (fail closed) |
| 5 | Authority DB path vs intended path mismatch | PASS (fail closed) |
| 6 | DB UUID mismatch | PASS (fail closed) |
| 7 | DB account_key mismatch | PASS (fail closed) |
| 8 | DB authority_id mismatch | PASS (fail closed) |
| 9 | DB deleted + recreated at same path | PASS (fail closed; create() also refuses) |
| 10 | two processes concurrent first bootstrap | PASS (one authority_id/db_uuid/domain) |
| 11 | corrupt/truncated Authority | PASS (fail closed; no fallback DB) |
| 12 | Authority lock contention (real OS processes) | PASS |
| 13 | Core 0.4 three-process formal invariants | PASS unchanged (433,489 / 4,461,994 / 0) |
| 14 | Python >=3.9, wheel clean install, Windows gates | PASS |

Plus P1-5 fail-closed extras: missing authority without bootstrap (no
adoption), runtime construction blocked on recreated DB, runtime-level
same-symbol exclusion through the shared certified DB, distinct-account
runtime domains.

## 4. Gates

```text
full pytest (3.12)         : 108 passed   (0.4.0 was 88; +20)
full pytest (3.9.13 wheel) : 108 passed
compileall -q src tests    : 0
wheel                      : qmt_execution_core-0.4.1-py3-none-any.whl
clean-env install          : OK
installed qmt-execution-core verify (out of tree, 3.12 + 3.9): PASS
  release_formal_verification   : PASS
  three_process_coordination    : 433,489 reachable states /
                                  4,461,994 interleaving edges /
                                  0 violations (identical to 0.4.0 —
                                  state machine untouched)
  implementation_refinement    : 0 hidden runtime state mutations /
                                  0 undeclared runtime events /
                                  0 illegal observation edges
  execution_source_sha256      : 4ab8173c735462bf64c506a1ba178e99f5b0d7777f10441bfa4973bdc1d5c763
                                 (identical between source tree and wheel)
ast.parse(feature_version=(3,9)) on every src file: NONE failed
Windows safety probes          : cross-process authority bootstrap + lock
                                 contention pass (real OS processes)
```

## 5. Compatibility note

No source/API incompatibility requiring a minor-version bump was found; the
delta is additive (`authority.py`, new exceptions, new optional config field,
new optional coordinator parameter). Version released as **0.4.1** as
specified.

## 6. Safety statement

- No real or simulation QMT order/cancel API was invoked.
- The Core 0.4 execution semantics (durable intent ordering, UNKNOWN/query-only
  recovery, ExecutionFinality, symbol exclusivity, shared cash, session-id
  leasing, live gates) are unchanged; the release formal gate numbers are
  byte-identical to 0.4.0.
