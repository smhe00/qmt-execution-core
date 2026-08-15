"""Minimal project-side integration example.

This file is documentation/example code. It does not enable real-money trading.
"""

from qmt_execution_core import (
    ExecutionLimits,
    ExecutionRequest,
    LimitExecutionGuard,
    PrecheckEvidence,
    SessionEvidence,
)
from qmt_execution_core.miniqmt import MiniQmtRuntime, MiniQmtRuntimeConfig


class ProjectGuard:
    def verify_session(self) -> SessionEvidence:
        # Replace with actual project/session checks.
        return SessionEvidence(
            ready=True,
            environment_verified=True,
            account_verified=True,
        )

    def verify(self, request: ExecutionRequest) -> PrecheckEvidence:
        # Replace with actual broker snapshot, position/cash and fresh quote
        # verification. Never set these True without real evidence.
        return PrecheckEvidence(
            allowed=False,
            environment_verified=True,
            account_verified=True,
            broker_snapshot_verified=False,
            position_verified=False,
            cash_verified=False,
            quote_verified=False,
            reason="example guard intentionally blocks execution",
        )


config = MiniQmtRuntimeConfig.from_json("runtime_config.local.json")
guard = LimitExecutionGuard(
    ProjectGuard(),
    ExecutionLimits(
        allowlist=frozenset({"510300.SH"}),
        max_order_qty=200,
        max_order_notional=5000.0,
    ),
)

# This connects/query/subscribes and opens the durable execution session.
# The example ProjectGuard above intentionally prevents any order submission.
runtime = MiniQmtRuntime.connect(config, guard=guard)
try:
    ...
finally:
    runtime.close()
