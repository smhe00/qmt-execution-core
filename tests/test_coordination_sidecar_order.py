from __future__ import annotations

from pathlib import Path

import pytest

from qmt_execution_core import (
    BrokerAsset,
    BrokerOrder,
    BrokerOrderStatus,
    ConservativeCashRequirementEstimator,
    CoordinatedExecutionSession,
    ExecutionFinality,
    ExecutionRequest,
    PrecheckEvidence,
    SQLiteExecutionCoordinator,
    SessionEvidence,
    Side,
    TradeState,
    execution_finality,
)


class Guard:
    def verify_session(self):
        return SessionEvidence(True, True, True)

    def verify(self, request):
        return PrecheckEvidence(True, True, True, True, True, True, True)


class Broker:
    def __init__(self) -> None:
        self.place_calls = 0
        self.order = None

    def execution_healthy(self):
        return True

    def query_asset(self):
        return BrokerAsset(100_000.0, 0.0, 0.0, 100_000.0)

    def place_order(self, request):
        self.place_calls += 1
        self.order = BrokerOrder(
            order_id=1,
            symbol=request.symbol,
            side=request.side,
            qty=request.qty,
            filled_qty=0,
            status=BrokerOrderStatus.WORKING,
            client_order_id=request.client_order_id,
            order_remark=request.order_remark,
            strategy_name=request.strategy_id,
        )
        return 1

    def query_order(self, order_id):
        return self.order

    def query_orders(self):
        return () if self.order is None else (self.order,)

    def cancel_order(self, order_id):
        raise AssertionError("not used")


def request():
    return ExecutionRequest("c1", "0700.HK", Side.BUY, 100, 10.0, "s", "s-c1")


def make_session(
    tmp_path: Path,
    broker: Broker,
    coordinator: SQLiteExecutionCoordinator,
    hook,
):
    return CoordinatedExecutionSession(
        broker=broker,
        guard=Guard(),
        journal_path=tmp_path / "journal.json",
        lock_path=tmp_path / "exec.lock",
        coordinator=coordinator,
        account_key="account-a",
        account_resource=broker,
        cash_estimator=ConservativeCashRequirementEstimator(safety_buffer=10.0),
        execution_id="s",
        before_broker_submit=hook,
    )


def test_coordination_commits_before_project_sidecar_and_broker(tmp_path: Path):
    broker = Broker()
    coordinator = SQLiteExecutionCoordinator(tmp_path / "coord.db")
    observed = []

    def hook(req):
        claim = coordinator.get_claim("account-a", req.symbol)
        assert claim is not None
        assert claim.execution_id == "s"
        assert coordinator.active_reserved_cash("account-a") == 1010.0
        assert broker.place_calls == 0
        observed.append("sidecar")

    session = make_session(tmp_path, broker, coordinator, hook)
    session.open()
    out = session.submit(request())
    assert out.state is TradeState.WORKING
    assert observed == ["sidecar"]
    assert broker.place_calls == 1
    session.close()


def test_project_sidecar_failure_releases_shared_resources_and_is_resolved_failed(tmp_path: Path):
    broker = Broker()
    coordinator = SQLiteExecutionCoordinator(tmp_path / "coord.db")

    def hook(req):
        assert coordinator.get_claim("account-a", req.symbol) is not None
        assert broker.place_calls == 0
        raise RuntimeError("project ledger commit failed")

    first = make_session(tmp_path, broker, coordinator, hook)
    first.open()
    with pytest.raises(RuntimeError):
        first.submit(request())
    assert first.machine.state is TradeState.FAILED
    assert execution_finality(first.machine) is ExecutionFinality.RESOLVED
    assert coordinator.get_claim("account-a", "0700.HK") is None
    assert coordinator.active_reserved_cash("account-a") == 0.0
    assert broker.place_calls == 0
    first.close()

    restarted = make_session(tmp_path, broker, coordinator, hook)
    out = restarted.open()
    assert out.state is TradeState.FAILED
    assert execution_finality(restarted.machine) is ExecutionFinality.RESOLVED
    assert coordinator.get_claim("account-a", "0700.HK") is None
    assert coordinator.active_reserved_cash("account-a") == 0.0
    assert broker.place_calls == 0
    restarted.close()
