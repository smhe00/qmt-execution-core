from __future__ import annotations

from collections.abc import Iterable

from .domain import BrokerOrder, BrokerOrderStatus, Side, TradeEvent, TradeState
from .exceptions import RecoveryAmbiguous


def find_unique_managed_order(
    orders: Iterable[BrokerOrder],
    *,
    symbol: str,
    side: Side,
    qty: int,
    order_remark: str,
) -> BrokerOrder:
    """Find the one broker order matching durable local identity.

    Zero matches are intentionally ambiguous: after a submission exception the
    order may have reached the broker but not yet be visible, so zero is never
    permission to resend automatically.
    """
    matches = [
        order
        for order in orders
        if order.order_remark == order_remark
        and order.symbol == symbol
        and order.side is side
        and order.qty == qty
    ]
    if len(matches) != 1:
        raise RecoveryAmbiguous(
            f"expected exactly one durable-identity broker match, got {len(matches)}"
        )
    return matches[0]


def event_for_observation(current: TradeState, status: BrokerOrderStatus) -> TradeEvent | None:
    """Map a normalized broker observation to a state-aware abstract event."""
    recovering = current in {TradeState.UNKNOWN, TradeState.CANCEL_REJECTED}

    if status is BrokerOrderStatus.UNKNOWN:
        return TradeEvent.QUERY_AMBIGUOUS

    if recovering:
        return {
            BrokerOrderStatus.ACCEPTED: TradeEvent.RECOVERY_ACCEPTED,
            BrokerOrderStatus.WORKING: TradeEvent.RECOVERY_WORKING,
            BrokerOrderStatus.PARTIALLY_FILLED: TradeEvent.RECOVERY_PARTIAL,
            BrokerOrderStatus.CANCEL_PENDING: TradeEvent.RECOVERY_CANCELLING,
            BrokerOrderStatus.PARTIAL_CANCELLED: TradeEvent.RECOVERY_CANCELLED,
            BrokerOrderStatus.CANCELLED: TradeEvent.RECOVERY_CANCELLED,
            BrokerOrderStatus.FILLED: TradeEvent.RECOVERY_FILLED,
            BrokerOrderStatus.REJECTED: TradeEvent.RECOVERY_REJECTED,
        }[status]

    if status is BrokerOrderStatus.ACCEPTED:
        if current is TradeState.ACCEPTED:
            return None
    elif status is BrokerOrderStatus.WORKING:
        if current in {
            TradeState.ACCEPTED,
            TradeState.WORKING,
            TradeState.PENDING_CANCEL,
            TradeState.CANCELLING,
        }:
            return TradeEvent.ORDER_WORKING
    elif status is BrokerOrderStatus.PARTIALLY_FILLED:
        if current in {
            TradeState.ACCEPTED,
            TradeState.WORKING,
            TradeState.PARTIALLY_FILLED,
            TradeState.PENDING_CANCEL,
            TradeState.CANCELLING,
        }:
            return TradeEvent.ORDER_PARTIAL
    elif status is BrokerOrderStatus.CANCEL_PENDING:
        if current is TradeState.PENDING_CANCEL:
            return TradeEvent.CANCEL_SENT
        if current is TradeState.CANCELLING:
            return TradeEvent.CANCEL_STILL_PENDING
    elif status in {BrokerOrderStatus.PARTIAL_CANCELLED, BrokerOrderStatus.CANCELLED}:
        if current in {
            TradeState.ACCEPTED,
            TradeState.WORKING,
            TradeState.PARTIALLY_FILLED,
            TradeState.PENDING_CANCEL,
            TradeState.CANCELLING,
        }:
            return TradeEvent.CANCEL_CONFIRMED
    elif status is BrokerOrderStatus.FILLED:
        if current in {
            TradeState.ACCEPTED,
            TradeState.WORKING,
            TradeState.PARTIALLY_FILLED,
            TradeState.PENDING_CANCEL,
            TradeState.CANCELLING,
        }:
            return TradeEvent.ORDER_FILLED
    elif status is BrokerOrderStatus.REJECTED:
        if current in {TradeState.ACCEPTED, TradeState.WORKING}:
            return TradeEvent.ORDER_REJECTED

    raise RecoveryAmbiguous(
        f"observation {status.value} is not a valid refinement of state {current.value}"
    )
