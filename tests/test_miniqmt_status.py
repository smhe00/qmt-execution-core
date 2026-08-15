import pytest

from qmt_execution_core.domain import BrokerOrderStatus, ExecutionRequest, Side
from qmt_execution_core.exceptions import BrokerQueryAmbiguous, BrokerSubmissionAmbiguous, BrokerSubmissionRejected
from qmt_execution_core.miniqmt.adapter import MiniQmtBrokerAdapter, QmtOrderConfig
from qmt_execution_core.miniqmt.status import normalize_qmt_order_status


@pytest.mark.parametrize(
    "raw,expected",
    [
        (48, BrokerOrderStatus.ACCEPTED),
        (49, BrokerOrderStatus.ACCEPTED),
        (50, BrokerOrderStatus.WORKING),
        (51, BrokerOrderStatus.CANCEL_PENDING),
        (52, BrokerOrderStatus.CANCEL_PENDING),
        (53, BrokerOrderStatus.PARTIAL_CANCELLED),
        (54, BrokerOrderStatus.CANCELLED),
        (55, BrokerOrderStatus.PARTIALLY_FILLED),
        (56, BrokerOrderStatus.FILLED),
        (57, BrokerOrderStatus.REJECTED),
        (255, BrokerOrderStatus.UNKNOWN),
        (999, BrokerOrderStatus.UNKNOWN),
        ("50", BrokerOrderStatus.UNKNOWN),
    ],
)
def test_qmt_status_mapping(raw, expected):
    assert normalize_qmt_order_status(raw) is expected


class RawOrder:
    def __init__(self, status=50, filled=0, qty=100):
        self.order_id = 101
        self.stock_code = "510300.SH"
        self.order_type = 23
        self.order_volume = qty
        self.traded_volume = filled
        self.order_status = status
        self.order_remark = "demo_1"
        self.traded_price = 4.7


class FakeTrader:
    def __init__(self):
        self.submit_result = 101
        self.cancel_result = 0
        self.order = RawOrder()
        self.orders_result = [self.order]

    def order_stock(self, *args):
        if isinstance(self.submit_result, Exception):
            raise self.submit_result
        return self.submit_result

    def cancel_order_stock(self, *args):
        return self.cancel_result

    def query_stock_order(self, *args):
        return self.order

    def query_stock_orders(self, *args):
        return self.orders_result


def adapter(trader=None):
    return MiniQmtBrokerAdapter(
        trader or FakeTrader(),
        object(),
        order_config=QmtOrderConfig(buy_order_type=23, sell_order_type=24, price_type=11),
        strategy_name="demo",
        query_delay_seconds=0,
    )


def request():
    return ExecutionRequest("c1", "510300.SH", Side.BUY, 100, 4.7, "demo", "demo_1")


def test_positive_order_id_is_not_fill_claim():
    assert adapter().place_order(request()) == 101


def test_minus_one_is_definitive_submit_reject():
    trader = FakeTrader()
    trader.submit_result = -1
    with pytest.raises(BrokerSubmissionRejected):
        adapter(trader).place_order(request())


def test_submit_exception_is_ambiguous():
    trader = FakeTrader()
    trader.submit_result = RuntimeError("disconnect")
    with pytest.raises(BrokerSubmissionAmbiguous):
        adapter(trader).place_order(request())


def test_query_none_is_ambiguous_not_empty():
    trader = FakeTrader()
    trader.orders_result = None
    with pytest.raises(BrokerQueryAmbiguous):
        adapter(trader).query_orders()


def test_52_requires_partial_fill_consistency():
    trader = FakeTrader()
    trader.order = RawOrder(status=52, filled=0)
    assert adapter(trader).query_order(101).status is BrokerOrderStatus.UNKNOWN


def test_53_preserves_partial_fill_and_is_terminal_cancelled_mapping():
    trader = FakeTrader()
    trader.order = RawOrder(status=53, filled=40)
    order = adapter(trader).query_order(101)
    assert order.status is BrokerOrderStatus.PARTIAL_CANCELLED
    assert order.filled_qty == 40
