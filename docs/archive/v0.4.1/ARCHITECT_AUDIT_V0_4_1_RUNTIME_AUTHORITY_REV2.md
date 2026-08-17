# Independent Architecture/Code Audit — Core 0.4.1 Runtime Authority Rev2

## Candidate

```text
branch: feature/0.4.1-runtime-authority
candidate: 54b2cbea09a3d5707a12861bb65964141e1cf0fd
baseline: acf20d9fe5cf2aede3cc0ad0e8936ecb0c5b2692
```

## Verdict

**CHANGES_REQUIRED**

Rev2 correctly closes the three blockers found in Rev1 at the `MiniQmtRuntimeConfig` level:

- production config no longer accepts `coordination_path`;
- production config no longer accepts `authority_root`;
- normal shared runtime resolves with `bootstrap=False`;
- first initialization is moved to an explicit bootstrap operation;
- `account_key` is recomputed from the account identity tuple;
- Authority-bound DBs require exactly one identity row.

However, the canonical Authority root is still not structurally unique on Windows.

## P1 blocker — Windows canonical root still trusts process environment

`default_authority_root()` currently does:

```python
base = os.environ.get("LOCALAPPDATA")
if base:
    return Path(base) / "qmt-execution-core" / "authority"
```

This directly contradicts the stated invariant that the root is not process-overridable.

Two independent strategy processes can run under the same Windows user and the same broker account while carrying different `LOCALAPPDATA` environment values:

```text
Process A: LOCALAPPDATA=C:\A
Process B: LOCALAPPDATA=D:\B

same account_key
    -> C:\A\qmt-execution-core\authority\<account_key>.authority.json
    -> D:\B\qmt-execution-core\authority\<account_key>.authority.json
```

Each process can therefore resolve a different Authority + coordination DB domain. This re-opens the same-account split-brain condition the Runtime Authority is intended to eliminate.

### Required fix

Production canonical-root derivation MUST come from the OS user identity, not mutable process environment.

Recommended Windows implementation:

```text
SHGetKnownFolderPath(FOLDERID_LocalAppData, ...)
```

via `ctypes` (or an equivalently authoritative Windows Known Folder API).

Requirements:

1. Do not use `%LOCALAPPDATA%`, `%USERPROFILE%`, `HOME`, `Path.home()`, or another process-environment-derived value as the production uniqueness authority.
2. If the OS Known Folder lookup fails, fail closed. Do not fall back to an environment-derived path.
3. Add a Windows regression proving that changing `os.environ["LOCALAPPDATA"]` does not change `default_authority_root()`.
4. Add a cross-process Windows regression where two processes have different `LOCALAPPDATA` values but resolve the same canonical root / Authority for the same OS user.

POSIX should follow the same principle: derive from the OS user database (`pwd.getpwuid(...)`). If authoritative user-home resolution fails, fail closed rather than falling back to `Path.home()`.

## P1 hardening — operator bootstrap must not create a noncanonical production Authority

Rev2 CLI currently exposes:

```text
qmt-execution-core bootstrap-authority --authority-root ...
```

A production operator can therefore create an Authority in a root that ordinary production runtime will never resolve.

For the uniqueness-guaranteed production CLI path, remove `--authority-root`. The operator bootstrap and normal runtime must call the same non-overridable canonical-root resolver.

Tests that need an isolated root should call the low-level `AccountRuntimeAuthority(temp_root)` API directly; test isolation is not a production CLI requirement.

## Low-level injection boundary

`MiniQmtRuntime.connect(coordinator=...)` / low-level component injection may remain for tests and explicitly unsafe legacy integration, but the uniqueness guarantee must be stated precisely:

> The Account Runtime Authority uniqueness guarantee applies to the production shared-runtime path. Supplying injected coordination/authority components opts out of that guarantee and must not be exposed by production strategy composition.

The TGrid follow-up audit must confirm its production builder does not expose or route these bypasses.

## Evidence accepted from Rev2 as regression baseline

Subject to re-run on the final candidate:

- `MiniQmtRuntimeConfig` production schema removes `coordination_path` / `authority_root`.
- normal shared runtime uses `bootstrap=False`.
- Authority+DB deletion causes normal runtime fail-closed rather than auto-rebootstrap.
- account identity tuple recomputation is implemented.
- dedicated DB identity cardinality is exactly one.
- Core execution state machine/formal model is unchanged.
- Rev2 self-certified `114 passed`; this is not final release evidence until a PR/CI run exists for the final audited head.

## Release gate after fix

Before PASS/merge:

```text
1. final candidate on separate PR
2. Python 3.9 / 3.11 / 3.12 CI PASS
3. Windows safety CI PASS
4. full pytest / compileall / wheel clean install PASS
5. installed qmt-execution-core verify PASS
6. three-process formal proof unchanged: 0 violations
7. Windows mutable-LOCALAPPDATA uniqueness regression PASS
8. normal runtime cannot bootstrap
9. production config cannot select coordination path/root
10. production bootstrap CLI cannot select authority root
11. no real/simulation QMT order/cancel
```

Only after all gates pass should Core 0.4.1 merge and TGrid pin the exact reviewed merge SHA.
