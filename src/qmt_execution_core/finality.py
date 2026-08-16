from __future__ import annotations

from enum import Enum

from .domain import TradeState
from .state_machine import MachineSnapshot


class ExecutionFinality(str, Enum):
    """Whether an execution is safe to release from shared symbol ownership."""

    OPEN = "open"
    RESOLVED = "resolved"
    QUARANTINED = "quarantined"


_OPEN_STATES = {
    TradeState.SUBMITTED,
    TradeState.ACCEPTED,
    TradeState.WORKING,
    TradeState.UNKNOWN,
    TradeState.PARTIALLY_FILLED,
    TradeState.PENDING_CANCEL,
    TradeState.CANCELLING,
    TradeState.CANCEL_REJECTED,
}

_RESOLVED_STATES = {
    TradeState.FILLED,
    TradeState.CANCELLED,
    TradeState.REJECTED,
}


def execution_finality(snapshot: MachineSnapshot) -> ExecutionFinality:
    """Derive v0.4 execution finality from the existing state + SafetyFacts.

    Finality is deliberately additive: the v0.3 state machine remains the
    source of execution lifecycle truth.  In particular, FAILED is not enough
    to release a same-symbol claim.  A FAILED snapshot that still carries
    ``unresolved_order`` is quarantined until authoritative reconciliation.
    """

    if not isinstance(snapshot, MachineSnapshot):
        raise TypeError("snapshot must be a MachineSnapshot")

    if snapshot.state is TradeState.FAILED:
        if snapshot.facts.unresolved_order:
            return ExecutionFinality.QUARANTINED
        return ExecutionFinality.RESOLVED

    if snapshot.state in _OPEN_STATES:
        return ExecutionFinality.OPEN

    if snapshot.state in _RESOLVED_STATES:
        return ExecutionFinality.RESOLVED

    # IDLE / WAIT_TRIGGER / TRIGGER / PRE_CHECK have no unresolved broker
    # lifecycle.  They therefore do not justify holding a shared symbol claim.
    return ExecutionFinality.RESOLVED
