from __future__ import annotations

from qmt_execution_core import (
    ExecutionRequest,
    ExecutionSession,
    PrecheckEvidence,
    SessionEvidence,
    Side,
    TradeState,
)
from qmt_execution_core.exceptions import BrokerSubmissionRejected


class Guard:
    def verify_session(self):
        return SessionEvidence(True, True, True)

    def verify(self, request):
        return PrecheckEvidence(True, True, True, True, True, True, True)


class Broker:
    def __init__(self) -> None:
        self.place_calls = 0

    def execution_healthy(self):
        return True

    def place_order(self, request):
        self.place_calls += 1
        raise AssertionError("broker side effect must not be reached")

    def query_order(self, order_id):
        raise AssertionError("not used")

    def query_orders(self):
        return ()

    def cancel_order(self, order_id):
        raise AssertionError("not used")


def test_pre_broker_rejected_is_terminal_without_submit_fact(tmp_path):
    broker = Broker()

    def reject_locally(request):
        raise BrokerSubmissionRejected("same account/symbol already claimed")

    session = ExecutionSession(
        broker=broker,
        guard=Guard(),
        journal_path=tmp_path / "journal.json",
        lock_path=tmp_path / "exec.lock",
        execution_id="s",
        before_submit_coordination=reject_locally,
    )
    session.open()
    out = session.submit(
        ExecutionRequest("c1", "0700.HK", Side.BUY, 100, 10.0, "s", "s-c1")
    )

    assert out.state is TradeState.REJECTED
    assert broker.place_calls == 0
    assert session.machine.facts.submitted_once is False
    assert session.machine.facts.unresolved_order is False
    assert session.machine.facts.terminal_order_confirmed is True
    session.close()
