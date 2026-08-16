# Independent Architecture/Code Audit — qmt-execution-core 0.4.1 Runtime Authority

> Reviewed candidate: `d4992543b7aa2496b2ba3fb7cd51b5cc74192a00`
> Branch: `feature/0.4.1-runtime-authority`
> Result: **CHANGES_REQUIRED**
> No real or simulation QMT order/cancel is authorized.

## Accepted work

The candidate correctly introduces the Account Runtime Authority model, persistent DB instance UUID, DB identity metadata, per-account OS-lock bootstrap, authority/DB identity mismatch rejection, and preserves the existing Core 0.4 execution/formal model. The reported 108-test result is retained as a regression baseline.

## P1 blockers

### P1-1 Production `authority_root` is still caller-selectable

`MiniQmtRuntimeConfig` exposes `authority_root` as a normal runtime/JSON configuration field. Two strategy processes for the same `account_key` can therefore select different roots and bootstrap two independent Authority files and two independent coordination DBs:

```text
same account_key
Strategy A -> authority_root A -> Authority A -> DB A
Strategy B -> authority_root B -> Authority B -> DB B
```

This recreates the original split-brain failure one level above `coordination_path`.

Required fix:
- production shared runtime must derive one non-overridable host/user canonical Authority root;
- strategy/runtime JSON must not be allowed to select a different root;
- test isolation may inject a root only through an explicitly test/low-level API that is not the production configuration path;
- add a negative regression proving same account + two attempted production roots cannot create two domains.

The canonical root itself should not depend on per-process-overridable environment configuration if that can make two processes derive different roots. On Windows prefer an OS-known user-local application-data location; on POSIX derive from the OS user identity/home rather than a process-local XDG/HOME override when feasible.

### P1-2 Normal `MiniQmtRuntime.connect()` automatically bootstraps Authority

The production shared-runtime path currently calls:

```text
AccountRuntimeAuthority.resolve(..., bootstrap=True)
```

for every normal connection. This violates the frozen requirement that a missing Authority during normal runtime is fail-closed.

Failure case:

```text
established Authority + coordination DB
-> both files/directories accidentally deleted
-> next normal runtime connect
-> automatically creates a new empty Authority/DB domain
```

The new domain has a new UUID but there is no old Authority left to compare against, so the runtime can silently lose the old cross-process claim/cash state.

Required fix:
- normal production runtime must use `bootstrap=False`;
- first initialization must be an explicit operator/bootstrap action, separate from ordinary strategy start;
- provide a dedicated bootstrap API/CLI or equivalent explicit provisioning path;
- missing Authority during normal runtime must fail closed;
- add regression: after deleting both established Authority and DB, normal runtime must refuse to start and must not create replacement files.

### P1-3 Production `coordination_path` still bypasses Authority

`MiniQmtRuntimeConfig.coordination_path` remains a normal production shared-runtime configuration field. In `MiniQmtRuntime.connect()`, if it is present the code constructs a legacy `SQLiteExecutionCoordinator` directly and bypasses Runtime Authority verification.

The low-level explicit-path coordinator may remain for compatibility/tests, but the production MiniQMT shared-runtime configuration must not expose a direct path that restores the 0.4.0 split-brain configuration hole.

Required fix:
- production `MiniQmtRuntimeConfig` shared path must resolve through Runtime Authority only;
- keep explicit-path support only as a clearly separated low-level/test compatibility API, not as the normal JSON/runtime configuration route;
- TGrid production wiring must have no `coordination_path` / `--coordination-db` selection after the reviewed Core release.

If removing the public production field is judged source-incompatible, stop and explicitly decide whether the safe release should be 0.5.0 rather than silently calling it 0.4.1.

## P2 hardening

- `AccountRuntimeAuthority.resolve()` accepts `account_key` and account identity fields independently. Recompute `account_key_from_binding_identity(environment, account_type, account_id_sha256)` internally and require exact equality so an Authority record cannot be created with a logically inconsistent identity tuple.
- Consider enforcing exactly one `coordination_identity` row in an Authority-bound dedicated DB rather than relying on `LIMIT 1`.
- Crash after DB creation but before Authority atomic replace currently leaves an orphan DB and safely blocks automatic adoption. This is fail-closed and not a release blocker, but document/manual recovery tooling should eventually cover it.

## Verification required after fixes

1. Full pytest + compileall.
2. Python 3.9 / 3.11 / 3.12 CI.
3. Windows cross-process Authority tests.
4. Wheel clean install + installed `qmt-execution-core verify`.
5. Existing 3-process state-space proof unchanged PASS.
6. New explicit regressions:
   - same account cannot select two production Authority roots;
   - normal runtime with missing Authority fails closed;
   - deleting both Authority + DB does not auto-bootstrap replacements;
   - production shared runtime cannot select arbitrary `coordination_path`;
   - explicit operator bootstrap creates exactly one Authority + DB under cross-process race;
   - after bootstrap, ordinary runtime only verifies and never creates/replaces Authority/DB.
7. No real or simulation QMT order/cancel.

## Release decision

**DO NOT MERGE `d4992543...`.**

After the above P1 items are fixed, return the new exact branch head for independent review. Only after PASS should a PR be opened/merged and TGrid pin the reviewed merge SHA.
