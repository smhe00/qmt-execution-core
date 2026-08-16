# Implementation Task — Core 0.4.1 Runtime Authority Rev3

## Status

AUTHORIZED FOR IMPLEMENTATION

## Baseline

```text
candidate: 54b2cbea09a3d5707a12861bb65964141e1cf0fd
branch: feature/0.4.1-runtime-authority
```

Independent audit:

```text
docs/ARCHITECT_AUDIT_V0_4_1_RUNTIME_AUTHORITY_REV2.md
```

No real or simulation QMT order/cancel is authorized.

## Required P1 fixes

### 1. Make the canonical production authority root OS-derived and non-overridable

Windows production root MUST be obtained from the Windows Known Folder API (`FOLDERID_LocalAppData`, e.g. `SHGetKnownFolderPath`) or an equivalent OS identity API.

Do not use or fall back to:

```text
LOCALAPPDATA
USERPROFILE
HOME
Path.home()
```

for the production uniqueness authority.

If authoritative OS root resolution fails, raise `RuntimeAuthorityError` / `RuntimeConfigurationError` and fail closed.

POSIX: resolve the OS user's home through the user database (`pwd.getpwuid(...)`). If that fails, fail closed; no environment/home fallback.

### 2. Remove production bootstrap root override

Remove `--authority-root` from the production `bootstrap-authority` CLI.

The operator bootstrap and normal runtime MUST call the exact same `default_authority_root()` implementation.

Tests needing a temporary root may use the low-level `AccountRuntimeAuthority(temp_root)` API directly.

### 3. Regression tests

Add at least:

- Windows: mutate `os.environ["LOCALAPPDATA"]` between calls; `default_authority_root()` remains identical.
- Windows: two real processes with different `LOCALAPPDATA` values resolve the same canonical root for the same OS user.
- Windows Known Folder lookup failure -> fail closed, no environment fallback.
- POSIX user-database lookup failure -> fail closed, no `Path.home()` fallback.
- `bootstrap-authority --help` has no `--authority-root` production option.
- explicit bootstrap followed by normal runtime verifies the same canonical Authority.

## Preserve Rev2 accepted fixes

Do not regress:

- no `MiniQmtRuntimeConfig.coordination_path` production route;
- no `MiniQmtRuntimeConfig.authority_root` production route;
- normal runtime `bootstrap=False`;
- explicit operator bootstrap only;
- account_key recomputation;
- exactly one DB identity row;
- persistent DB UUID / authority_id binding;
- cross-process bootstrap locking;
- Core 0.4 execution/finality/formal semantics.

## Final release gates

Open a PR before requesting final audit. The final PR head must have:

```text
full pytest PASS
compileall PASS
wheel build + clean install PASS
installed qmt-execution-core verify PASS
Python 3.9 / 3.11 / 3.12 CI PASS
Windows safety CI PASS
three-process formal model 0 violations
mutable-LOCALAPPDATA regression PASS
no real/simulation QMT order/cancel
```

Return with the exact PR number and head SHA. Do not merge before independent audit PASS.
