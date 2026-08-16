from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradeState(str, Enum):
    IDLE = "idle"
    WAIT_TRIGGER = "wait_trigger"
    TRIGGER = "trigger"
    PRE_CHECK = "pre_check"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    WORKING = "working"
    UNKNOWN = "unknown"
    PARTIALLY_FILLED = "partially_filled"
    PENDING_CANCEL = "pending_cancel"
    CANCELLING = "cancelling"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    CANCEL_REJECTED = "cancel_rejected"
    FAILED = "failed"


class TradeEvent(str, Enum):
    SESSION_READY = "session_ready"
    TRIGGERED = "triggered"
    BEGIN_PRECHECK = "begin_precheck"
    PRECHECK_VERIFIED = "precheck_verified"
    PRECHECK_REJECTED = "precheck_rejected"
    INTENT_PERSISTED = "intent_persisted"
    PRE_BROKER_REJECTED = "pre_broker_rejected"
    PRE_BROKER_ABORTED = "pre_broker_aborted"
    SUBMIT_ACCEPTED = "submit_accepted"
    SUBMIT_REJECTED = "submit_rejected"
    SUBMIT_AMBIGUOUS = "submit_ambiguous"
    ORDER_WORKING = "order_working"
    ORDER_PARTIAL = "order_partial"
    ORDER_FILLED = "order_filled"
    ORDER_REJECTED = "order_rejected"
    CANCEL_REQUESTED = "cancel_requested"
    CANCEL_SENT = "cancel_sent"
    CANCEL_REQUEST_REJECTED = "cancel_request_rejected"
    CANCEL_STILL_PENDING = "cancel_still_pending"
    CANCEL_CONFIRMED = "cancel_confirmed"
    QUERY_AMBIGUOUS = "query_ambiguous"
    RECOVERY_ACCEPTED = "recovery_accepted"
    RECOVERY_WORKING = "recovery_working"
    RECOVERY_PARTIAL = "recovery_partial"
    RECOVERY_CANCELLING = "recovery_cancelling"
    RECOVERY_FILLED = "recovery_filled"
    RECOVERY_CANCELLED = "recovery_cancelled"
    RECOVERY_REJECTED = "recovery_rejected"
    RECOVERY_FAILED = "recovery_failed"
    RESTART_RECOVERY = "restart_recovery"
    NEXT_CYCLE = "next_cycle"
    FATAL = "fatal"


class BrokerOrderStatus(str, Enum):
    ACCEPTED = "accepted"
    WORKING = "working"
    PARTIALLY_FILLED = "partially_filled"
    CANCEL_PENDING = "cancel_pending"
    PARTIAL_CANCELLED = "partial_cancelled"
    CANCELLED = "cancelled"
    FILLED = "filled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class CancelRequestResult(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True)
class SafetyFacts:
    environment_verified: bool = False
    account_verified: bool = False
    broker_snapshot_verified: bool = False
    position_verified: bool = False
    cash_verified: bool = False
    quote_verified: bool = False
    intent_persisted: bool = False
    reservation_persisted: bool = False
    unresolved_order: bool = False
    terminal_order_confirmed: bool = False
    submitted_once: bool = False
    cancel_intent_persisted: bool = False


@dataclass(frozen=True)
class SessionEvidence:
    ready: bool
    environment_verified: bool
    account_verified: bool
    reason: str = ""

    def validate(self) -> None:
        for name in ("ready", "environment_verified", "account_verified"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a plain bool")
        if type(self.reason) is not str:
            raise TypeError("reason must be a string")
        if self.ready and not (self.environment_verified and self.account_verified):
            raise ValueError("ready session must carry environment/account verification")


@dataclass(frozen=True)
class PrecheckEvidence:
    allowed: bool
    environment_verified: bool
    account_verified: bool
    broker_snapshot_verified: bool
    position_verified: bool
    cash_verified: bool
    quote_verified: bool
    reason: str = ""

    def validate(self) -> None:
        for name in (
            "allowed",
            "environment_verified",
            "account_verified",
            "broker_snapshot_verified",
            "position_verified",
            "cash_verified",
            "quote_verified",
        ):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a plain bool")
        if type(self.reason) is not str:
            raise TypeError("reason must be a string")
        if self.allowed and not (
            self.environment_verified
            and self.account_verified
            and self.broker_snapshot_verified
            and self.position_verified
            and self.cash_verified
            and self.quote_verified
        ):
            raise ValueError("allowed precheck must carry complete verification evidence")


@dataclass(frozen=True)
class ExecutionRequest:
    client_order_id: str
    symbol: str
    side: Side | str
    qty: int
    limit_price: float
    strategy_id: str
    order_remark: str

    def __post_init__(self) -> None:
        for name in ("client_order_id", "symbol", "strategy_id", "order_remark"):
            value = getattr(self, name)
            if type(value) is not str or not value:
                raise ValueError(f"{name} must be a non-empty string")
        side = self.side if isinstance(self.side, Side) else Side(self.side)
        object.__setattr__(self, "side", side)
        if type(self.qty) is not int or self.qty <= 0:
            raise ValueError("qty must be a positive plain int")
        if type(self.limit_price) not in (int, float) or isinstance(self.limit_price, bool):
            raise ValueError("limit_price must be a plain number")
        if not isfinite(float(self.limit_price)) or self.limit_price <= 0:
            raise ValueError("limit_price must be finite and positive")


@dataclass(frozen=True)
class BrokerOrder:
    order_id: int
    symbol: str
    side: Side
    qty: int
    filled_qty: int
    status: BrokerOrderStatus
    order_remark: str = ""
    client_order_id: str = ""
    strategy_name: str = ""
    order_sysid: str = ""
    status_message: str = ""
    average_fill_price: float | None = None

    def __post_init__(self) -> None:
        if type(self.order_id) is not int or self.order_id <= 0:
            raise ValueError("order_id must be a positive plain int")
        if type(self.symbol) is not str or not self.symbol:
            raise ValueError("symbol must be a non-empty string")
        if not isinstance(self.side, Side):
            object.__setattr__(self, "side", Side(self.side))
        if type(self.qty) is not int or self.qty <= 0:
            raise ValueError("qty must be a positive plain int")
        if type(self.filled_qty) is not int or not 0 <= self.filled_qty <= self.qty:
            raise ValueError("filled_qty must be within [0, qty]")
        if not isinstance(self.status, BrokerOrderStatus):
            object.__setattr__(self, "status", BrokerOrderStatus(self.status))
        for name in (
            "order_remark",
            "client_order_id",
            "strategy_name",
            "order_sysid",
            "status_message",
        ):
            if type(getattr(self, name)) is not str:
                raise ValueError(f"{name} must be a string")
        if self.average_fill_price is not None:
            if type(self.average_fill_price) not in (int, float) or isinstance(
                self.average_fill_price, bool
            ):
                raise ValueError("average_fill_price must be numeric or None")
            if not isfinite(float(self.average_fill_price)) or self.average_fill_price < 0:
                raise ValueError("average_fill_price must be finite and non-negative")


@dataclass(frozen=True)
class BrokerAsset:
    cash: float
    frozen_cash: float
    market_value: float
    total_asset: float

    def __post_init__(self) -> None:
        for name in ("cash", "frozen_cash", "market_value", "total_asset"):
            value = getattr(self, name)
            if type(value) not in (int, float) or isinstance(value, bool):
                raise ValueError(f"{name} must be numeric")
            if not isfinite(float(value)) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    volume: int
    can_use_volume: int
    frozen_volume: int = 0
    market_value: float = 0.0
    average_price: float = 0.0

    def __post_init__(self) -> None:
        if type(self.symbol) is not str or not self.symbol:
            raise ValueError("symbol must be a non-empty string")
        for name in ("volume", "can_use_volume", "frozen_volume"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative plain int")
        if self.can_use_volume > self.volume:
            raise ValueError("can_use_volume cannot exceed volume")
        for name in ("market_value", "average_price"):
            value = getattr(self, name)
            if type(value) not in (int, float) or isinstance(value, bool):
                raise ValueError(f"{name} must be numeric")
            if not isfinite(float(value)) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")


@dataclass(frozen=True)
class BrokerTrade:
    order_id: int
    symbol: str
    side: Side
    traded_volume: int
    traded_price: float
    traded_amount: float = 0.0
    traded_id: str = ""
    strategy_name: str = ""
    order_remark: str = ""

    def __post_init__(self) -> None:
        if type(self.order_id) is not int or self.order_id <= 0:
            raise ValueError("order_id must be a positive plain int")
        if type(self.symbol) is not str or not self.symbol:
            raise ValueError("symbol must be non-empty")
        if not isinstance(self.side, Side):
            object.__setattr__(self, "side", Side(self.side))
        if type(self.traded_volume) is not int or self.traded_volume <= 0:
            raise ValueError("traded_volume must be positive")
        for name in ("traded_price", "traded_amount"):
            value = getattr(self, name)
            if type(value) not in (int, float) or isinstance(value, bool):
                raise ValueError(f"{name} must be numeric")
            if not isfinite(float(value)) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        for name in ("traded_id", "strategy_name", "order_remark"):
            if type(getattr(self, name)) is not str:
                raise ValueError(f"{name} must be a string")


@dataclass(frozen=True)
class ExecutionSnapshot:
    state: TradeState
    client_order_id: str | None = None
    broker_order_id: int | None = None
    ordered_qty: int = 0
    filled_qty: int = 0
    average_fill_price: float | None = None
    reason: str = ""
