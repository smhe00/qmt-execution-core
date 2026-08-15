# Production MiniQMT Runtime

`MiniQmtRuntime` is the production-shaped integration layer around the generic
execution kernel. It is still strategy independent.

## Lifecycle

```text
load strict runtime config
→ verify QMT userdata path
→ load fingerprint-only account binding
→ start callback EventQueue
→ construct/register XtQuant callback
→ trader.start()
→ trader.connect() == exact int 0
→ query_account_infos + query_account_status
→ select exactly one fingerprint-bound securities account
→ subscribe(account) == exact int 0
→ open ExecutionSession
→ acquire execution mutex
→ load/create journal
→ verify state-machine/source binding
→ restart reconciliation if needed
→ mark recovery complete
→ allow simulation orders
```

For `environment="live"` an additional double gate applies:

```text
live_trading_enabled == true
AND
runtime.confirm_live(token)
```

The plaintext runtime token is never persisted by this package. Store only its
SHA-256 digest in local configuration.

## Account binding

The binding file contains no plaintext account id and no plaintext QMT path:

```json
{
  "schema_version": 1,
  "environment": "simulation",
  "account_type": 2,
  "account_id_sha256": "...",
  "qmt_path_sha256": "..."
}
```

Create one locally:

```bash
qmt-execution-core create-binding \
  --environment simulation \
  --account-type 2 \
  --qmt-path "C:/.../userdata_mini" \
  --output config/account-binding.local.json
```

If `--account-id` is omitted, the CLI prompts for it.

## Runtime config

Example:

```json
{
  "schema_version": 1,
  "environment": "simulation",
  "qmt_path": "C:/.../userdata_mini",
  "binding_path": "account-binding.local.json",
  "journal_path": "../runtime/demo-journal.json",
  "lock_path": "../runtime/demo-execution.lock",
  "strategy_name": "demo",
  "live_trading_enabled": false
}
```

Relative paths in a JSON config are resolved relative to the config file.

## Project integration

A project supplies an `ExecutionGuard`:

```python
class ProjectGuard:
    def verify_session(self) -> SessionEvidence:
        ...

    def verify(self, request: ExecutionRequest) -> PrecheckEvidence:
        ...
```

The project guard is responsible for project-specific evidence such as:

- trading-day / time-window policy;
- fresh quote verification;
- available cash / quantity;
- Core-position or other portfolio invariants;
- project-specific risk budget.

Common hard limits can be composed with `LimitExecutionGuard`.

## Disconnect recovery

Callbacks never directly reconcile or send orders. `on_disconnected()` only
produces an immutable event which the serial event queue uses to invalidate
execution health.

Recovery is explicit:

```text
runtime.recover_after_disconnect()
```

It performs:

```text
connect exact success
→ exact bound account id/type/status verification
→ re-subscribe exact success
→ authoritative durable execution reconciliation
→ project session evidence re-verification
→ mark recovery complete
```

For live execution it additionally requires a fresh runtime token:

```python
runtime.recover_after_disconnect(runtime_token="...")
```

An `ACCOUNT_STATUS_OK` callback alone never restores order capability.

## Safety boundary

The runtime deliberately does not:

- generate trading signals;
- decide target portfolio weights;
- change Core holdings;
- silently choose an account;
- silently convert query `None` to an empty list;
- automatically resend UNKNOWN submissions;
- persist plaintext live confirmation tokens.


## Cross-project serialization

The production runtime automatically acquires a **QMT-path-scoped runtime mutex** derived from the normalized QMT userdata-path fingerprint before constructing/starting `XtQuantTrader`. This lock is independent of each project's journal `lock_path`. Therefore two different projects cannot accidentally run separate active QMT runtimes against the same userdata path by choosing different project lock files.

If multiple strategies must share one account concurrently, run them behind one execution gateway/daemon rather than starting multiple `MiniQmtRuntime` processes.
