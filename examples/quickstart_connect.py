"""Beginner-safe MiniQMT connection check.

This example connects to the configured simulation account and then closes.
The guard intentionally rejects every execution request, so this script cannot
place an order through qmt-execution-core.
"""

from qmt_execution_core import (
    ExecutionLimits,
    ExecutionRequest,
    LimitExecutionGuard,
    PrecheckEvidence,
    SessionEvidence,
)
from qmt_execution_core.miniqmt import MiniQmtRuntime, MiniQmtRuntimeConfig


class QuickStartBlockingGuard:
    def verify_session(self) -> SessionEvidence:
        return SessionEvidence(
            ready=True,
            environment_verified=True,
            account_verified=True,
        )

    def verify(self, request: ExecutionRequest) -> PrecheckEvidence:
        return PrecheckEvidence(
            allowed=False,
            environment_verified=True,
            account_verified=True,
            broker_snapshot_verified=False,
            position_verified=False,
            cash_verified=False,
            quote_verified=False,
            reason="Quick Start guard intentionally blocks all orders",
        )


def main() -> int:
    config = MiniQmtRuntimeConfig.from_json("runtime_config.local.json")
    if config.environment != "simulation" or config.live_trading_enabled:
        raise RuntimeError(
            "Quick Start only accepts environment=simulation and "
            "live_trading_enabled=false"
        )

    guard = LimitExecutionGuard(
        QuickStartBlockingGuard(),
        ExecutionLimits(
            allowlist=frozenset(),
            max_order_qty=1,
            max_order_notional=1.0,
        ),
    )

    runtime = MiniQmtRuntime.connect(config, guard=guard)
    try:
        print("[PASS] Core runtime connected to MiniQMT simulation account")
        print(f"[PASS] execution_healthy = {runtime.execution_healthy}")
        if not runtime.execution_healthy:
            raise RuntimeError("runtime connected but execution_healthy is false")
    finally:
        runtime.close()

    print("[PASS] Runtime closed cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
