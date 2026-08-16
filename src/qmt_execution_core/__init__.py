"""Reusable fail-closed trading execution core."""

from .authority import (
    AccountAuthority,
    AccountRuntimeAuthority,
    default_authority_root,
)
from .coordinated_session import CoordinatedExecutionSession
from .coordination import (
    CashRequirementEstimate,
    CashRequirementEstimator,
    CashReservation,
    ConservativeCashRequirementEstimator,
    CoordinationDbIdentity,
    ExecutionCoordinator,
    SQLiteExecutionCoordinator,
    SymbolClaim,
    account_key_from_binding_identity,
)
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
from .exceptions import (
    CoordinationIdentityError,
    RuntimeAuthorityError,
)
from .finality import ExecutionFinality, execution_finality
from .guards import ExecutionLimits, LimitExecutionGuard
from .ports import AccountResourcePort, BrokerPort, ExecutionGuard
from .session import ExecutionSession
from .verifier import verify_release_model, verify_state_machine

__all__ = [
    "AccountAuthority",
    "AccountResourcePort",
    "AccountRuntimeAuthority",
    "BrokerAsset",
    "BrokerOrder",
    "BrokerOrderStatus",
    "BrokerPort",
    "BrokerPosition",
    "BrokerTrade",
    "CancelRequestResult",
    "CashRequirementEstimate",
    "CashRequirementEstimator",
    "CashReservation",
    "ConservativeCashRequirementEstimator",
    "CoordinatedExecutionSession",
    "CoordinationDbIdentity",
    "CoordinationIdentityError",
    "EventQueueState",
    "ExecutionCoordinator",
    "ExecutionFinality",
    "ExecutionGuard",
    "ExecutionLimits",
    "ExecutionRequest",
    "ExecutionSession",
    "ExecutionSnapshot",
    "LimitExecutionGuard",
    "PrecheckEvidence",
    "RuntimeAuthorityError",
    "SQLiteExecutionCoordinator",
    "SafetyFacts",
    "SerialEventQueue",
    "SessionEvidence",
    "Side",
    "SymbolClaim",
    "TradeEvent",
    "TradeState",
    "account_key_from_binding_identity",
    "default_authority_root",
    "execution_finality",
    "verify_release_model",
    "verify_state_machine",
]
