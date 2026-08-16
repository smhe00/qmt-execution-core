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


def session(tmp_path: Path, broker=None, guard=None, **hooks):
    return ExecutionSession(
        broker=broker or FakeBroker(),
        guard=guard or AllowGuard(),
        journal_path=tmp_path / "journal.json",
        lock_path=tmp_path / "exec.lock",
        execution_id="test",
        **hooks,
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


def test_before_submit_hook_runs_after_durable_intent_before_broker(tmp_path):
    # Phase A sidecar: the hook fires with the core durable intent already
    # persisted and broker.place_order NOT yet invoked (ordering invariant).
    broker = FakeBroker()
    observed = []

    def hook(request):
        assert request.client_order_id == "c1"
        assert broker.place_calls == 0  # before broker side effect
        assert s.journal.data.get("intent") is not None  # after durable intent
        observed.append(("hook", broker.place_calls))

    s = session(tmp_path, broker=broker, before_broker_submit=hook)
    s.open()
    out = s.submit(req())
    assert out.state is TradeState.WORKING
    assert broker.place_calls == 1
    assert observed == [("hook", 0)]
    s.close()


def test_before_submit_hook_failure_proves_broker_not_called(tmp_path):
    # Phase A fail-closed: a raised pre-submit hook proves broker.place_order
    # was never invoked; restart recovery fails closed with NO blind resend.
    broker = FakeBroker()

    def hook(request):
        raise RuntimeError("tgrid durable ledger commit failed")

    s = session(tmp_path, broker=broker, before_broker_submit=hook)
    s.open()
    with pytest.raises(RuntimeError):
        s.submit(req())
    assert broker.place_calls == 0
    s.close()

    s2 = session(tmp_path, broker=broker, before_broker_submit=hook)
    out = s2.open()
    assert out.state is TradeState.FAILED  # fail closed, no blind resend
    assert broker.place_calls == 0
    s2.close()


def test_before_cancel_hook_ordering(tmp_path):
    # Phase A pre-cancel sidecar: fires after durable cancel intent, before
    # the broker cancel side effect; the durable order is still WORKING when
    # the hook observes it.
    broker = FakeBroker()
    seen = []

    def hook(order_id):
        assert broker.orders[order_id].status is BrokerOrderStatus.WORKING
        seen.append(order_id)

    s = session(tmp_path, broker=broker, before_broker_cancel=hook)
    s.open()
    out = s.submit(req())
    oid = out.broker_order_id
    s.cancel()
    assert seen == [oid]
    s.close()


def test_before_cancel_hook_failure_proves_cancel_not_called(tmp_path):
    # Phase A fail-closed: a raised pre-cancel hook proves broker.cancel_order
    # was never invoked (order stays WORKING).
    broker = FakeBroker()

    def fail_hook(order_id):
        raise RuntimeError("tgrid cancel accounting failed")

    s = session(tmp_path, broker=broker, before_broker_cancel=fail_hook)
    s.open()
    out = s.submit(req())
    oid = out.broker_order_id
    with pytest.raises(RuntimeError):
        s.cancel()
    assert broker.orders[oid].status is BrokerOrderStatus.WORKING
    s.close()


def test_hooks_are_noop_by_default(tmp_path):
    # Backward compatibility: no hooks -> identical submit/cancel lifecycle.
    broker = FakeBroker()
    s = session(tmp_path, broker=broker)
    s.open()
    out = s.submit(req())
    assert out.state is TradeState.WORKING
    assert s.cancel().state is TradeState.CANCELLING
    s.close()
