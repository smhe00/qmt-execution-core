from __future__ import annotations

from dataclasses import dataclass
from time import sleep

from ..domain import (
    BrokerOrder,
    BrokerOrderStatus,
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
        return cls(
            buy_order_type=int(getattr(xtconstant, "STOCK_BUY")),
            sell_order_type=int(getattr(xtconstant, "STOCK_SELL")),
            price_type=int(price_type),
        )


class MiniQmtBrokerAdapter:
    """Dependency-injected MiniQMT/XtQuant BrokerPort implementation.

    This module does not import `xtquant` itself. Production code supplies an
    already constructed trader/account and QMT constant values, while tests can
    use fakes without MiniQMT installed.
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
    ) -> None:
        if type(strategy_name) is not str or not strategy_name:
            raise ValueError("strategy_name must be a non-empty string")
        if type(query_attempts) is not int or query_attempts <= 0:
            raise ValueError("query_attempts must be a positive plain int")
        self._trader = trader
        self._account = account
        self._config = order_config
        self._strategy_name = strategy_name
        self._query_attempts = query_attempts
        self._query_delay_seconds = float(query_delay_seconds)
        self._connected = True

    def mark_disconnected(self) -> None:
        self._connected = False

    def mark_connected(self) -> None:
        self._connected = True

    def execution_healthy(self) -> bool:
        return self._connected

    def place_order(self, request: ExecutionRequest) -> int:
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
        except Exception as exc:  # broker boundary
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
            # Caller must re-query the original order; do not infer cancellation.
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

    def _strict_query(self, fn, *, label: str):
        last_exc: Exception | None = None
        for attempt in range(self._query_attempts):
            try:
                value = fn()
            except Exception as exc:  # broker boundary
                last_exc = exc
                value = None
            if value is not None:
                return value
            if attempt + 1 < self._query_attempts:
                sleep(self._query_delay_seconds)
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
            avg_price_raw = getattr(raw, "traded_price", None)
            average_fill_price = None if avg_price_raw is None else float(avg_price_raw)
        except (TypeError, ValueError, AttributeError) as exc:
            raise BrokerQueryAmbiguous("MiniQMT order payload is malformed") from exc

        if raw_order_type == self._config.buy_order_type:
            side = Side.BUY
        elif raw_order_type == self._config.sell_order_type:
            side = Side.SELL
        else:
            raise BrokerQueryAmbiguous("MiniQMT order side/type is not recognized")

        status = normalize_qmt_order_status(raw_status)
        # Strengthen raw-state consistency. Inconsistent payloads become UNKNOWN
        # instead of silently trusting a contradictory status.
        if status in {BrokerOrderStatus.PARTIALLY_FILLED, BrokerOrderStatus.PARTIAL_CANCELLED}:
            if not 0 < filled_qty < qty:
                status = BrokerOrderStatus.UNKNOWN
        if status is BrokerOrderStatus.CANCEL_PENDING and int(raw_status) == 52:
            if not 0 < filled_qty < qty:
                status = BrokerOrderStatus.UNKNOWN
        if status is BrokerOrderStatus.FILLED and filled_qty != qty:
            status = BrokerOrderStatus.UNKNOWN

        return BrokerOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            qty=qty,
            filled_qty=filled_qty,
            status=status,
            order_remark=remark,
            average_fill_price=average_fill_price,
        )
