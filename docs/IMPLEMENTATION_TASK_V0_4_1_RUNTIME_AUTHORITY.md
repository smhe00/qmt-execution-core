# Implementation Task — qmt-execution-core 0.4.1 Runtime Authority

## Status

AUTHORIZED FOR IMPLEMENTATION

## Owner

DSH / implementation agent. All implementation evidence remains self-certified until independent architect audit.

## Baseline

```text
qmt-execution-core 0.4.0
acf20d9fe5cf2aede3cc0ad0e8936ecb0c5b2692
```

Authoritative delta spec:

```text
docs/CORE_0_4_1_RUNTIME_AUTHORITY_SPEC.md
```

No real or simulation QMT order/cancel is authorized.

## Objective

Close the remaining split-brain configuration hole in Core 0.4.0:

```text
same account + two different configured coordination_path
```

must no longer be a possible production shared-runtime configuration.

Implement an account-scoped Runtime Authority that certifies exactly one dedicated coordination DB instance by canonical path + persistent DB UUID.

## Required implementation

### P1-1 Account Runtime Authority model

Add a generic Core model/store for:

```text
schema_version
authority_id
account_key
environment
account_type
account_id_sha256
coordination_db_path
coordination_db_uuid
```

Authority path must be derived from `account_key` under a canonical host/user Core authority root. Production strategy code must not choose the Authority filename.

### P1-2 Dedicated DB identity metadata

Authority-bound coordination DB must persist:

```text
account_key
db_uuid
authority_id
schema_version
```

The UUID is generated once for a DB instance and remains stable across normal content changes.

### P1-3 Atomic bootstrap

Provide a bootstrap path protected by an OS-backed per-account authority lock. Two independent processes racing first initialization must converge on one Authority + one DB UUID/domain.

Do not silently adopt/rewrite a mismatched existing Authority or DB.

### P1-4 Production shared-runtime resolution

Production shared runtime flow must be:

```text
actual account binding
→ account_key
→ canonical authority path
→ authority verification
→ certified DB path open
→ DB identity verification
→ coordinator construction
```

Do not trust a strategy-supplied arbitrary `coordination_path` as proof of uniqueness.

Legacy explicit-path low-level API may remain for compatibility/tests, but it must not be the production uniqueness-guaranteed path.

### P1-5 Fail-closed mismatch matrix

At minimum reject:

- account_key mismatch;
- environment/account identity mismatch;
- canonical DB path mismatch;
- DB UUID mismatch;
- DB account_key mismatch;
- DB authority_id mismatch;
- corrupted/truncated authority file;
- DB recreated at same path with a new UUID;
- missing authority during normal non-bootstrap runtime;
- conflicting concurrent bootstrap.

### P1-6 Preserve Core 0.4 semantics

Do not weaken:

- durable intent ordering;
- UNKNOWN/query-only recovery;
- ExecutionFinality;
- `(account_key, symbol)` unresolved exclusivity;
- shared BUY cash reservation;
- bounded session-id leasing;
- live double gate/account binding;
- callback isolation;
- Python >=3.9;
- formal verifier/refinement gate.

## Formal and test requirements

Run and record:

```text
full pytest
compileall
wheel build + clean install
installed qmt-execution-core verify
Python 3.9 / 3.11 / 3.12 CI
Windows safety probes
```

Add cross-process tests for Authority bootstrap and verification.

The existing three-process state-space proof need not model filesystem bytes, but the release gate must still pass unchanged after Authority authentication is inserted before coordinator use. Add runtime/refinement tests proving no broker side effect can occur before Authority + DB identity verification succeeds.

## Release discipline

Implement on a separate branch/PR. Do not merge until independent architecture/code audit PASS.

Expected release: `0.4.1` unless implementation discovers a source/API incompatibility that requires a minor-version bump; if so stop and escalate rather than silently relabel.

After reviewed Core merge, TGrid must pin the exact reviewed merge SHA and remove production `coordination_path` / Gate-6 `--coordination-db` selection in favor of Account Runtime Authority resolution.
