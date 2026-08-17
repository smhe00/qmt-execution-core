# Archived Documentation

This directory contains superseded specifications, implementation tasks, audits, evidence, and pre-consolidation user/runtime guides.

These files are preserved for historical traceability. They are **not** the current product or usage entrypoints.

Current authoritative documents:

- [`../USER_GUIDE.md`](../USER_GUIDE.md) — first-time setup, safe MiniQMT connection, strategy / coding-agent integration.
- [`../SPECIFICATION.md`](../SPECIFICATION.md) — current product contract and safety invariants.
- [`../OPERATIONS.md`](../OPERATIONS.md) — runtime operations, recovery, Runtime Authority, live gate and fault handling.

Archive layout:

```text
v0.4/
  frozen 0.4 specification, architecture, state-machine/runtime profiles,
  audits and implementation evidence

v0.4.1/
  Runtime Authority delta specification, audits, implementation tasks/evidence,
  and the pre-consolidation QUICK_START / USER_API documents
```

When archived material conflicts with the current three documents, follow the current documents and current source/tests. Do not use archived `coordination_path` guidance for production shared runtime in Core 0.4.1.
