from __future__ import annotations

from dataclasses import dataclass
from time import sleep
from typing import Callable

from ..domain import (
    BrokerAsset,
    BrokerOrder,
    BrokerOrderStatus,
    BrokerPosition,
    BrokerTrade,
    CancelRequestResult,
    ExecutionRequest,
    Side,
)
from ..exceptions import (
    BrokerQueryAmbiguous,
    BrokerSubmissionAmbiguous,
    BrokerSubmissionRejected,
)
from .status import normalize_qmt_order_status


@dataclass(frozen=True)
class QmtOrderConfig:
    buy_order_type: int
    sell_order_type: int
    price_type: int

    def __post_init__(self) -> None:
        for name in ("buy_order_type", "sell_order_type", "price_type"):
            if type(getattr(self, name)) is not int:
                raise TypeError(f"{name} must be a plain int")

    @classmethod
    def from_xtconstant(cls, xtconstant: object, *, price_type: int) -> "QmtOrderConfig":
        buy = getattr(xtconstant, "STOCK_BUY", None)
        sell = getattr(xtconstant, "STOCK_SELL", None)
        if type(buy) is not int or type(sell) is not int or type(price_type) is not int:
            raise TypeError("MiniQMT order constants must be plain ints")
        return cls(
            buy_order_type=buy,
            sell_order_type=sell,
            price_type=price_type,
        )


class MiniQmtBrokerAdapter:
    """Dependency-injected MiniQMT/XtQuant broker implementation.

    The adapter deliberately does not import ``xtquant``. A production runtime
    supplies the concrete trader/account/constants, while tests can use fakes.

    Queries remain available while order execution is unhealthy because they
    are required for fail-closed recovery. New orders require transport health,
    post-connect recovery completion, event-queue health, and the optional
    runtime execution gate.
    """

    def __init__(
        self,
        trader: object,
        account: object,
        *,
        order_config: QmtOrderConfig,
        strategy_name: str,
        query_attempts: int = 3,
        query_delay_seconds: float = 0.15,
        health_probe: Callable[[], bool] | None = None,
        order_gate: Callable[[], bool] | None = None,
        initially_connected: bool = True,
        recovery_ready: bool = True,
    ) -> None:
        if type(strategy_name) is not str or not strategy_name:
            raise ValueError("strategy_name must be a non-empty string")
        if type(query_attempts) is not int or query_attempts <= 0:
            raise ValueError("query_attempts must be a positive plain int")
        if type(initially_connected) is not bool or type(recovery_ready) is not bool:
            raise TypeError("connection/recovery flags must be bool")
        self._trader = trader
        self._account = account
        self._config = order_config
        self._strategy_name = strategy_name
        self._query_attempts = query_attempts
        self._query_delay_seconds = float(query_delay_seconds)
        self._transport_connected = initially_connected
        self._recovery_ready = recovery_ready
        self._health_probe = health_probe
        self._order_gate = order_gate

    @property
    def account(self) -> object:
        return self._account

    @property
    def trader(self) -> object:
        return self._trader

    def mark_disconnected(self) -> None:
        self._transport_connected = False
        self._recovery_ready = False

    def mark_transport_connected(self) -> None:
        self._transport_connected = True
        self._recovery_ready = False

    def mark_recovery_required(self) -> None:
        self._recovery_ready = False

    def mark_recovery_complete(self) -> None:
        if not self._transport_connected:
            raise RuntimeError("cannot complete recovery while transport is disconnected")
        self._recovery_ready = True

    def execution_healthy(self) -> bool:
        if not (self._transport_connected and self._recovery_ready):
            return False
        if self._health_probe is not None and self._health_probe() is not True:
            return False
        if self._order_gate is not None and self._order_gate() is not True:
            return False
        return True

    def place_order(self, request: ExecutionRequest) -> int:
        if not self.execution_healthy():
            raise BrokerSubmissionRejected(
                "MiniQMT execution gate is not ready; order was not invoked"
            )
        _validate_qmt_remark(request.order_remark)
        order_type = (
            self._config.buy_order_type
            if request.side is Side.BUY
            else self._config.sell_order_type
        )
        try:
            result = self._trader.order_stock(
                self._account,
                request.symbol,
                order_type,
                request.qty,
                self._config.price_type,
                float(request.limit_price),
                self._strategy_name,
                request.order_remark,
            )
        except Exception as exc:
            raise BrokerSubmissionAmbiguous("MiniQMT submit raised; outcome unknown") from exc
        if type(result) is not int:
            raise BrokerSubmissionAmbiguous("MiniQMT submit returned non-int result")
        if result == -1:
            raise BrokerSubmissionRejected("MiniQMT definitively rejected submit")
        if result <= 0:
            raise BrokerSubmissionAmbiguous("MiniQMT submit returned unexpected order id")
        return result

    def cancel_order(self, order_id: int) -> CancelRequestResult:
        if type(order_id) is not int or order_id <= 0:
            raise ValueError("order_id must be a positive plain int")
        try:
            result = self._trader.cancel_order_stock(self._account, order_id)
        except Exception:
            return CancelRequestResult.REJECTED
        if type(result) is int and result == 0:
            return CancelRequestResult.ACCEPTED
        return CancelRequestResult.REJECTED

    def query_order(self, order_id: int) -> BrokerOrder:
        if type(order_id) is not int or order_id <= 0:
            raise ValueError("order_id must be a positive plain int")
        raw = self._strict_query(
            lambda: self._trader.query_stock_order(self._account, order_id),
            label="query_stock_order",
        )
        return self._to_broker_order(raw)

    def query_orders(self) -> tuple[BrokerOrder, ...]:
        raw = self._strict_query(
            lambda: self._trader.query_stock_orders(self._account, False),
            label="query_stock_orders",
        )
        if not isinstance(raw, (list, tuple)):
            raise BrokerQueryAmbiguous("query_stock_orders returned unexpected type")
        return tuple(self._to_broker_order(item) for item in raw)

    def query_asset(self) -> BrokerAsset:
        raw = self._strict_query(
            lambda: self._trader.query_stock_asset(self._account),
            label="query_stock_asset",
        )
        try:
            return BrokerAsset(
                cash=float(getattr(raw, "cash")),
                frozen_cash=float(getattr(raw, "frozen_cash")),
                market_value=float(getattr(raw, "market_value")),
                total_asset=float(getattr(raw, "total_asset")),
            )
        except (TypeError, ValueError, AttributeError) as exc:
            raise BrokerQueryAmbiguous("MiniQMT asset payload is malformed") from exc

    def query_positions(self) -> tuple[BrokerPosition, ...]:
        raw = self._strict_query(
            lambda: self._trader.query_stock_positions(self._account),
            label="query_stock_positions",
        )
        if not isinstance(raw, (list, tuple)):
            raise BrokerQueryAmbiguous("query_stock_positions returned unexpected type")
        return tuple(self._to_broker_position(item) for item in raw)

    def query_trades(self) -> tuple[BrokerTrade, ...]:
        raw = self._strict_query(
            lambda: self._trader.query_stock_trades(self._account),
            label="query_stock_trades",
        )
        if not isinstance(raw, (list, tuple)):
            raise BrokerQueryAmbiguous("query_stock_trades returned unexpected type")
        return tuple(self._to_broker_trade(item) for item in raw)

    def _strict_query(self, fn, *, label: str):
        last_exc: Exception | None = None
        for attempt in range(self._query_attempts):
            try:
                value = fn()
            except Exception as exc:
                last_exc = exc
                value = None
            if value is not None:
                return value
            if attempt + 1 < self._query_attempts:
                sleep(max(0.0, self._query_delay_seconds))
        raise BrokerQueryAmbiguous(f"{label} remained ambiguous") from last_exc

    def _to_broker_order(self, raw: object) -> BrokerOrder:
        try:
            order_id = int(getattr(raw, "order_id"))
            symbol = str(getattr(raw, "stock_code"))
            raw_order_type = int(getattr(raw, "order_type"))
            qty = int(getattr(raw, "order_volume"))
            filled_qty = int(getattr(raw, "traded_volume"))
            raw_status = getattr(raw, "order_status")
            remark = str(getattr(raw, "order_remark", "") or "")
            strategy_name = str(getattr(raw, "strategy_name", "") or "")
            order_sysid = str(getattr(raw, "order_sysid", "") or "")
            status_message = str(getattr(raw, "status_msg", "") or "")
            avg_price_raw = getattr(raw, "traded_price", None)
            average_fill_price = None if avg_price_raw is None else float(avg_price_raw)
        except (TypeError, ValueError, AttributeError) as exc:
            raise BrokerQueryAmbiguous("MiniQMT order payload is malformed") from exc

        side = self._side_from_qmt_order_type(raw_order_type)
        status = normalize_qmt_order_status(raw_status)

        if status in {
            BrokerOrderStatus.PARTIALLY_FILLED,
            BrokerOrderStatus.PARTIAL_CANCELLED,
        } and not 0 < filled_qty < qty:
            status = BrokerOrderStatus.UNKNOWN
        if status is BrokerOrderStatus.CANCEL_PENDING and _plain_int(raw_status) == 52:
            if not 0 < filled_qty < qty:
                status = BrokerOrderStatus.UNKNOWN
        if status is BrokerOrderStatus.FILLED and filled_qty != qty:
            status = BrokerOrderStatus.UNKNOWN
        if status in {
            BrokerOrderStatus.ACCEPTED,
            BrokerOrderStatus.WORKING,
            BrokerOrderStatus.CANCELLED,
            BrokerOrderStatus.REJECTED,
        } and not 0 <= filled_qty <= qty:
            status = BrokerOrderStatus.UNKNOWN

        return BrokerOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            qty=qty,
            filled_qty=filled_qty,
            status=status,
            order_remark=remark,
            strategy_name=strategy_name,
            order_sysid=order_sysid,
            status_message=status_message,
            average_fill_price=average_fill_price,
        )

    def _to_broker_position(self, raw: object) -> BrokerPosition:
        try:
            return BrokerPosition(
                symbol=str(getattr(raw, "stock_code")),
                volume=int(getattr(raw, "volume")),
                can_use_volume=int(getattr(raw, "can_use_volume")),
                frozen_volume=int(getattr(raw, "frozen_volume", 0)),
                market_value=float(getattr(raw, "market_value", 0.0)),
                average_price=float(getattr(raw, "avg_price", 0.0)),
            )
        except (TypeError, ValueError, AttributeError) as exc:
            raise BrokerQueryAmbiguous("MiniQMT position payload is malformed") from exc

    def _to_broker_trade(self, raw: object) -> BrokerTrade:
        try:
            order_type = int(getattr(raw, "order_type"))
            return BrokerTrade(
                order_id=int(getattr(raw, "order_id")),
                symbol=str(getattr(raw, "stock_code")),
                side=self._side_from_qmt_order_type(order_type),
                traded_volume=int(getattr(raw, "traded_volume")),
                traded_price=float(getattr(raw, "traded_price")),
                traded_amount=float(getattr(raw, "traded_amount", 0.0)),
                traded_id=str(getattr(raw, "traded_id", "") or ""),
                strategy_name=str(getattr(raw, "strategy_name", "") or ""),
                order_remark=str(getattr(raw, "order_remark", "") or ""),
            )
        except (TypeError, ValueError, AttributeError) as exc:
            raise BrokerQueryAmbiguous("MiniQMT trade payload is malformed") from exc

    def _side_from_qmt_order_type(self, raw_order_type: int) -> Side:
        if raw_order_type == self._config.buy_order_type:
            return Side.BUY
        if raw_order_type == self._config.sell_order_type:
            return Side.SELL
        raise BrokerQueryAmbiguous("MiniQMT order side/type is not recognized")


def _plain_int(value: object) -> int | None:
    return value if type(value) is int else None


def _validate_qmt_remark(remark: str) -> None:
    try:
        encoded = remark.encode("ascii")
    except UnicodeEncodeError as exc:
        raise BrokerSubmissionRejected(
            "MiniQMT order_remark must be ASCII for deterministic identity"
        ) from exc
    if len(encoded) > 24:
        raise BrokerSubmissionRejected("MiniQMT order_remark exceeds 24 characters")
