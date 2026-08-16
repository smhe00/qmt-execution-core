# Implementation Task — qmt-execution-core Runtime Authority Hardening

## Status

**CHANGES_REQUIRED** after independent audit of candidate:

```text
d4992543b7aa2496b2ba3fb7cd51b5cc74192a00
```

Authoritative audit:

```text
docs/ARCHITECT_AUDIT_V0_4_1_RUNTIME_AUTHORITY.md
```

## Owner

DSH / implementation agent.

No real or simulation QMT order/cancel is authorized.

## Accepted baseline

The candidate's existing Runtime Authority model, DB UUID identity, cross-process Authority lock/bootstrap machinery, Core 0.4 execution semantics and reported 108-test suite are retained as the regression baseline. Do not discard them.

## Required P1 fixes

### P1-1 Make the production Authority root actually unique

`MiniQmtRuntimeConfig.authority_root` must not remain a strategy-selectable production/JSON option. Otherwise the same account can use two roots and create two independent Authority/DB domains.

Production shared runtime must derive one canonical host/user Authority root. Test isolation may inject another root only through a clearly separated test/low-level path that cannot be mistaken for production runtime configuration.

Add a negative test proving two production runtimes for the same account cannot select two different Authority roots.

### P1-2 Separate explicit bootstrap from normal runtime start

Normal production `MiniQmtRuntime.connect()` must **not** call `resolve(..., bootstrap=True)`.

Required lifecycle:

```text
explicit operator bootstrap
  -> create/lock/certify Authority + dedicated DB once

normal strategy runtime
  -> resolve(..., bootstrap=False)
  -> verify Authority + DB identity only
```

If Authority is missing during normal runtime, fail closed.

Required regression:

```text
established Authority + DB
-> delete both
-> normal runtime start
-> FAIL CLOSED
-> no new Authority
-> no new DB
```

Provide a dedicated explicit bootstrap API/CLI or equivalent operator provisioning entrypoint.

### P1-3 Remove the production `coordination_path` bypass

`MiniQmtRuntimeConfig.coordination_path` must not remain a normal production shared-runtime path that directly constructs `SQLiteExecutionCoordinator` and bypasses Authority verification.

Low-level explicit-path coordinator support may remain for isolated tests/compatibility, but production `MiniQmtRuntime` shared execution must resolve through Runtime Authority.

If removing/changing this public production configuration is source-incompatible, stop and explicitly decide whether the safe release is `0.5.0` rather than silently retaining the unsafe bypass under `0.4.1`.

## P2 hardening

- Recompute `account_key_from_binding_identity(environment, account_type, account_id_sha256)` inside Authority resolution and require it equals the supplied account_key.
- Prefer enforcing exactly one Authority identity row per dedicated coordination DB instead of `LIMIT 1` over a potentially multi-row table.
- Document the fail-closed orphan-DB case if a crash occurs after DB creation but before Authority atomic replace.

## Required verification after fixes

- full pytest;
- compileall;
- Python 3.9 / 3.11 / 3.12 CI;
- Windows cross-process Authority bootstrap/lock tests;
- wheel build + clean install;
- installed `qmt-execution-core verify`;
- existing three-process formal state-space proof unchanged PASS;
- production same-account/two-root split-brain attempt rejected;
- missing Authority on normal runtime rejected;
- deleting both Authority+DB does not auto-create replacements;
- production shared runtime cannot select arbitrary `coordination_path`;
- explicit bootstrap race converges to one Authority/DB UUID;
- zero real or simulation QMT order/cancel.

## Release discipline

Do not merge candidate `d4992543...`.

Return a new exact branch head for independent architecture/code audit. Only after audit PASS may a PR be merged and TGrid updated to the reviewed merge SHA.
