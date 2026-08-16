# Core 0.4.1 Runtime Authority — Independent Architecture/Code Audit (Rev3 Final)

Date: 2026-08-16
Candidate branch: `feature/0.4.1-runtime-authority`
Audited code baseline: `5cb66f336bbc7d0ac4fef202d1c7b5392251ba2e`
Clean release head before this docs-only audit record: `e95e897eede6d60964055d38a72785e1747bed99`
Base: Core 0.4.0 `acf20d9fe5cf2aede3cc0ad0e8936ecb0c5b2692`

## Verdict

**PASS FOR MERGE**

No remaining P0/P1 architecture or execution-safety blocker was found.

## Audited invariants

1. Same authoritative account identity derives the same `account_key`.
2. Production runtime configuration cannot choose `coordination_path` or `authority_root`.
3. Production canonical Authority root is OS-derived and fail-closed:
   - Windows: `FOLDERID_LocalAppData` via `SHGetKnownFolderPath`, not mutable `LOCALAPPDATA` / `USERPROFILE` / `HOME`.
   - POSIX: `pwd.getpwuid(os.getuid())`, not mutable `$HOME` / XDG / `Path.home()`.
4. Normal shared runtime is verify-only (`bootstrap=False`). Missing/corrupt Authority does not create a replacement domain.
5. First initialization is a separate operator bootstrap action and uses the same canonical root resolver as normal runtime.
6. Authority binds one dedicated coordination DB instance by canonical path + persistent `db_uuid` + `authority_id` + `account_key`.
7. Authority-bound DB verification requires exactly one identity row and exact identity match.
8. Deleting/recreating the DB at the same path does not preserve authority: UUID/identity mismatch fails closed.
9. Concurrent first bootstrap remains protected by the per-account OS lock.
10. Core 0.4 execution semantics are unchanged: UNKNOWN/query-only recovery, finality, per-(account,symbol) claim, shared BUY cash, bounded MiniQMT session IDs, live gate and callback isolation.

## Rev3-specific verification

Rev3 fixes the final split-brain hole from Rev2:

- mutable `LOCALAPPDATA` cannot change the Windows canonical root;
- two real Windows processes with different `LOCALAPPDATA` resolve the same root;
- Known Folder lookup failure fails closed;
- POSIX user-db lookup failure fails closed;
- bootstrap CLI no longer exposes `--authority-root`;
- production runtime config no longer exposes `coordination_path` / `authority_root`;
- normal runtime does not auto-bootstrap.

## Formal / CI evidence

Clean release head `e95e897eede6d60964055d38a72785e1747bed99`:

- GitHub Actions run `31933056148`: PASS
- Windows safety: PASS
- Python 3.9 / 3.11 / 3.12: PASS
- pytest / compileall / wheel build / clean reinstall: PASS
- installed `qmt-execution-core verify`: PASS
- 3-process formal product model remains: 433,489 reachable global states / 4,461,994 interleaving edges / 0 violations
- implementation refinement remains: 0 hidden state mutation / 0 undeclared runtime event / 0 illegal broker-observation edge

## Release hygiene

The initial PR tree accidentally contained one generated wheel under `%temp%/...`. It was removed before final approval and `*.whl` / `%temp%/` were added to `.gitignore`. The clean release tree contains no committed wheel artifact.

## Explicit boundary

`MiniQmtRuntime.connect(coordinator=...)` and `connect(authority=...)` remain dependency-injection / low-level test seams. Passing either explicitly opts out of the production Runtime-Authority uniqueness guarantee. Production compositions, including TGrid, MUST call shared `MiniQmtRuntime.connect()` with neither override and MUST bootstrap via the canonical operator bootstrap path.

This boundary is acceptable for Core 0.4.1 because the production configuration schema contains no path/root override and the frozen delta specification explicitly permits low-level compatibility/test seams. TGrid follow-up audit must prove its production composition does not inject either override.

## Merge authorization

PR #4 may be merged only if:

- its head contains this audit record plus the already-audited code and release-hygiene cleanup;
- CI for that final head remains green;
- no additional semantic code change is introduced after this audit.

After merge, TGrid must pin the exact Core merge SHA and remove its production `coordination_path` / Gate-6 `--coordination-db` path selection.
