"""Reusable fail-closed trading execution core."""

from .domain import (
    BrokerAsset,
    BrokerOrder,
    BrokerOrderStatus,
    BrokerPosition,
    BrokerTrade,
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
from .event_queue import EventQueueState, SerialEventQueue
from .guards import ExecutionLimits, LimitExecutionGuard
from .session import ExecutionSession
from .verifier import verify_state_machine

__all__ = [
    "BrokerAsset",
    "BrokerOrder",
    "BrokerOrderStatus",
    "BrokerPosition",
    "BrokerTrade",
    "CancelRequestResult",
    "EventQueueState",
    "ExecutionLimits",
    "ExecutionRequest",
    "ExecutionSession",
    "ExecutionSnapshot",
    "LimitExecutionGuard",
    "PrecheckEvidence",
    "SafetyFacts",
    "SerialEventQueue",
    "SessionEvidence",
    "Side",
    "TradeEvent",
    "TradeState",
    "verify_state_machine",
]
