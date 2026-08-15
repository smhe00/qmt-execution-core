from dataclasses import replace
from pathlib import Path

import pytest

from qmt_execution_core.domain import (
    BrokerOrder,
    BrokerOrderStatus,
    CancelRequestResult,
    ExecutionRequest,
    PrecheckEvidence,
    SessionEvidence,
    Side,
    TradeState,
)
from qmt_execution_core.exceptions import BrokerSubmissionAmbiguous, BrokerSubmissionRejected, SessionClosedError
from qmt_execution_core.session import ExecutionSession


class AllowGuard:
    def verify_session(self):
        return SessionEvidence(True, True, True)

    def verify(self, request):
        return PrecheckEvidence(True, True, True, True, True, True, True)


class RejectGuard(AllowGuard):
    def verify(self, request):
        return PrecheckEvidence(False, True, True, False, False, False, False, "risk rejected")


class FakeBroker:
    def __init__(self):
        self.orders = {}
        self.next_id = 100
        self.submit_mode = "ok"
        self.cancel_result = CancelRequestResult.ACCEPTED
        self.healthy = True
        self.place_calls = 0

    def execution_healthy(self):
        return self.healthy

    def place_order(self, request):
        self.place_calls += 1
        if self.submit_mode == "reject":
            raise BrokerSubmissionRejected("rejected")
        if self.submit_mode == "ambiguous_before":
            raise BrokerSubmissionAmbiguous("disconnect before visibility")
        self.next_id += 1
        oid = self.next_id
        self.orders[oid] = BrokerOrder(
            order_id=oid,
            symbol=request.symbol,
            side=request.side,
            qty=request.qty,
            filled_qty=0,
            status=BrokerOrderStatus.WORKING,
            order_remark=request.order_remark,
        )
        if self.submit_mode == "ambiguous_after":
            raise BrokerSubmissionAmbiguous("disconnect after broker accepted")
        return oid

    def cancel_order(self, order_id):
        if self.cancel_result is CancelRequestResult.ACCEPTED:
            self.orders[order_id] = replace(self.orders[order_id], status=BrokerOrderStatus.CANCEL_PENDING)
        return self.cancel_result

    def query_order(self, order_id):
        return self.orders[order_id]

    def query_orders(self):
        return tuple(self.orders.values())


def req():
    return ExecutionRequest("c1", "510300.SH", Side.BUY, 100, 4.7, "demo", "demo_1")


def session(tmp_path: Path, broker=None, guard=None):
    return ExecutionSession(
        broker=broker or FakeBroker(),
        guard=guard or AllowGuard(),
        journal_path=tmp_path / "journal.json",
        lock_path=tmp_path / "exec.lock",
        execution_id="test",
    )


def test_precheck_reject_never_calls_broker(tmp_path):
    broker = FakeBroker()
    s = session(tmp_path, broker=broker, guard=RejectGuard())
    s.open()
    out = s.submit(req())
    assert out.state is TradeState.REJECTED
    assert broker.place_calls == 0
    s.close()


def test_submit_poll_and_fill(tmp_path):
    broker = FakeBroker()
    s = session(tmp_path, broker=broker)
    assert s.open().state is TradeState.WAIT_TRIGGER
    out = s.submit(req())
    assert out.state is TradeState.WORKING
    oid = out.broker_order_id
    broker.orders[oid] = replace(
        broker.orders[oid],
        status=BrokerOrderStatus.FILLED,
        filled_qty=100,
        average_fill_price=4.69,
    )
    out = s.poll()
    assert out.state is TradeState.FILLED
    assert out.filled_qty == 100
    s.close()


def test_cancel_ack_is_not_cancelled_until_query_confirms(tmp_path):
    broker = FakeBroker()
    s = session(tmp_path, broker=broker)
    s.open()
    out = s.submit(req())
    oid = out.broker_order_id
    out = s.cancel()
    assert out.state is TradeState.CANCELLING
    broker.orders[oid] = replace(broker.orders[oid], status=BrokerOrderStatus.CANCELLED)
    out = s.poll()
    assert out.state is TradeState.CANCELLED
    s.close()


def test_partial_cancel_preserves_fill(tmp_path):
    broker = FakeBroker()
    s = session(tmp_path, broker=broker)
    s.open()
    out = s.submit(req())
    oid = out.broker_order_id
    broker.orders[oid] = replace(
        broker.orders[oid], status=BrokerOrderStatus.PARTIALLY_FILLED, filled_qty=40
    )
    assert s.poll().state is TradeState.PARTIALLY_FILLED
    assert s.cancel().state is TradeState.CANCELLING
    broker.orders[oid] = replace(
        broker.orders[oid], status=BrokerOrderStatus.PARTIAL_CANCELLED, filled_qty=40
    )
    out = s.poll()
    assert out.state is TradeState.CANCELLED
    assert out.filled_qty == 40
    s.close()


def test_submit_unknown_recovers_by_durable_remark_without_resend(tmp_path):
    broker = FakeBroker()
    broker.submit_mode = "ambiguous_after"
    s = session(tmp_path, broker=broker)
    s.open()
    out = s.submit(req())
    assert out.state is TradeState.UNKNOWN
    assert broker.place_calls == 1
    out = s.poll()
    assert out.state is TradeState.WORKING
    assert broker.place_calls == 1
    s.close()


def test_zero_match_after_ambiguous_submit_fails_closed_no_resend(tmp_path):
    broker = FakeBroker()
    broker.submit_mode = "ambiguous_before"
    s = session(tmp_path, broker=broker)
    s.open()
    assert s.submit(req()).state is TradeState.UNKNOWN
    assert s.poll().state is TradeState.FAILED
    assert broker.place_calls == 1
    s.close()


def test_restart_recovers_working_order(tmp_path):
    broker = FakeBroker()
    s1 = session(tmp_path, broker=broker)
    s1.open()
    out = s1.submit(req())
    assert out.state is TradeState.WORKING
    s1.close()

    s2 = session(tmp_path, broker=broker)
    out = s2.open()
    assert out.state is TradeState.WORKING
    s2.close()


def test_cancel_rejected_requires_requery(tmp_path):
    # V4-B / V5 gap closure: a rejected cancel ack is NOT terminal
    # cancellation; the order must be re-queried (never assumed) and only a
    # confirmed broker state resolves it.
    broker = FakeBroker()
    broker.cancel_result = CancelRequestResult.REJECTED
    s = session(tmp_path, broker=broker)
    s.open()
    out = s.submit(req())
    oid = out.broker_order_id
    assert out.state is TradeState.WORKING
    out = s.cancel()
    # rejected ack must not be treated as cancelled
    assert out.state is not TradeState.CANCELLED
    assert broker.orders[oid].status is BrokerOrderStatus.WORKING
    # broker confirms the cancel on a later re-query -> poll resolves it
    broker.orders[oid] = replace(broker.orders[oid], status=BrokerOrderStatus.CANCELLED)
    out = s.poll()
    assert out.state is TradeState.CANCELLED
    s.close()


def test_restart_recovers_cancel_pending(tmp_path):
    # V5 gap closure: a crash during a cancel (order CANCEL_PENDING /
    # machine CANCELLING) is recovered on restart by re-query, and the
    # confirmed cancel resolves to CANCELLED.
    broker = FakeBroker()
    s1 = session(tmp_path, broker=broker)
    s1.open()
    out = s1.submit(req())
    oid = out.broker_order_id
    assert out.state is TradeState.WORKING
    out = s1.cancel()
    assert out.state is TradeState.CANCELLING
    assert broker.orders[oid].status is BrokerOrderStatus.CANCEL_PENDING
    s1.close()

    s2 = session(tmp_path, broker=broker)
    out = s2.open()
    assert out.state is TradeState.CANCELLING  # recovered mid-cancel
    broker.orders[oid] = replace(broker.orders[oid], status=BrokerOrderStatus.CANCELLED)
    out = s2.poll()
    assert out.state is TradeState.CANCELLED
    s2.close()


def test_release_lock_closes_session_for_new_orders(tmp_path):
    s = session(tmp_path)
    s.open()
    s.close()
    with pytest.raises(SessionClosedError):
        s.submit(req())
