"""Reusable fail-closed trading execution core."""

from .domain import (
    BrokerOrder,
    BrokerOrderStatus,
    CancelRequestResult,
    ExecutionRequest,
    ExecutionSnapshot,
    PrecheckEvidence,
    SafetyFacts,
    SessionEvidence,
    Side,
    TradeEvent,
    TradeState,
)
from .session import ExecutionSession
from .verifier import verify_state_machine

__all__ = [
    "BrokerOrder",
    "BrokerOrderStatus",
    "CancelRequestResult",
    "ExecutionRequest",
    "ExecutionSession",
    "ExecutionSnapshot",
    "PrecheckEvidence",
    "SafetyFacts",
    "SessionEvidence",
    "Side",
    "TradeEvent",
    "TradeState",
    "verify_state_machine",
]
