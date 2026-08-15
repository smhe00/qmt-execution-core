# Agent Operating Contract

This repository is a reusable execution-infrastructure component, not a trading strategy.

## Preserve these boundaries

- Do not add TGrid, grid, CorePosition, ETF allocation, repo-repurchase timing, or other project-specific strategy semantics to `src/qmt_execution_core`.
- The generic core must not import `xtquant`; MiniQMT integration belongs under `miniqmt/` and is dependency injected.
- `UNKNOWN` / ambiguous submission may only recover through broker query/reconciliation; never auto-resubmit.
- Broker callbacks only emit immutable observations. They do not mutate strategy state, journal, reservations, or send orders.
- Durable intent/reservation data is written before broker submit.
- Durable cancel intent is written before broker cancel.
- Cancel API success only means the request was sent; terminal cancellation requires broker re-query/confirmation.
- Missing protected execution source files must fail formal verification.
- Formal-model verification does not replace runtime refinement tests.

## Before changing execution semantics

Run:

```bash
python -m pytest
python -m compileall -q src tests
PYTHONPATH=src python -c "from qmt_execution_core import verify_state_machine; print(verify_state_machine())"
```

Any state/event change requires corresponding verifier and refinement-test updates.

## Production-live boundary

The initial repository intentionally does not contain a production real-money bootstrap or automatic live-trading enable switch. Add such capabilities only as a separately reviewed layer.
