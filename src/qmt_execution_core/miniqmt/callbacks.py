from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class QmtOrderObserved:
    order_id: int
    stock_code: str
    order_status: int
    order_volume: int
    traded_volume: int
    order_remark: str


@dataclass(frozen=True)
class QmtTradeObserved:
    order_id: int
    stock_code: str
    traded_volume: int
    traded_price: float


@dataclass(frozen=True)
class QmtBrokerDisconnected:
    reason: str = "disconnected"


@dataclass(frozen=True)
class QmtOrderErrorObserved:
    order_id: int | None
    error_id: int | None
    error_msg: str


@dataclass(frozen=True)
class QmtCancelErrorObserved:
    order_id: int | None
    error_id: int | None
    error_msg: str


class QmtCallbackBridge:
    """Callback isolation bridge.

    Every callback only constructs an immutable observation and forwards it to
    `emit`. No strategy state, journal, reservation, retry or broker call is
    allowed here.
    """

    def __init__(self, emit: Callable[[object], None]) -> None:
        self._emit = emit

    def on_stock_order(self, order: object) -> None:
        self._emit(QmtOrderObserved(
            order_id=int(getattr(order, "order_id")),
            stock_code=str(getattr(order, "stock_code")),
            order_status=int(getattr(order, "order_status")),
            order_volume=int(getattr(order, "order_volume")),
            traded_volume=int(getattr(order, "traded_volume")),
            order_remark=str(getattr(order, "order_remark", "") or ""),
        ))

    def on_stock_trade(self, trade: object) -> None:
        self._emit(QmtTradeObserved(
            order_id=int(getattr(trade, "order_id")),
            stock_code=str(getattr(trade, "stock_code")),
            traded_volume=int(getattr(trade, "traded_volume")),
            traded_price=float(getattr(trade, "traded_price")),
        ))

    def on_disconnected(self) -> None:
        self._emit(QmtBrokerDisconnected())

    def on_order_error(self, error: object) -> None:
        self._emit(QmtOrderErrorObserved(
            order_id=_optional_int(getattr(error, "order_id", None)),
            error_id=_optional_int(getattr(error, "error_id", None)),
            error_msg=str(getattr(error, "error_msg", "") or ""),
        ))

    def on_cancel_error(self, error: object) -> None:
        self._emit(QmtCancelErrorObserved(
            order_id=_optional_int(getattr(error, "order_id", None)),
            error_id=_optional_int(getattr(error, "error_id", None)),
            error_msg=str(getattr(error, "error_msg", "") or ""),
        ))


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
