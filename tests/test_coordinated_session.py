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
)
from qmt_execution_core.exceptions import BrokerQueryAmbiguous, SymbolClaimConflict


class AllowGuard:
    def verify_session(self):
        return SessionEvidence(True, True, True)

    def verify(self, request):
        return PrecheckEvidence(True, True, True, True, True, True, True)


class Broker:
    def __init__(self) -> None:
        self.order = None
        self.place_calls = 0
        self.query_ambiguous = False

    def execution_healthy(self):
        return True

    def query_asset(self):
        return BrokerAsset(100_000.0, 0.0, 0.0, 100_000.0)

    def place_order(self, request):
        self.place_calls += 1
        self.order = BrokerOrder(
            order_id=101,
            symbol=request.symbol,
            side=request.side,
            qty=request.qty,
            filled_qty=0,
            status=BrokerOrderStatus.WORKING,
            order_remark=request.order_remark,
            client_order_id=request.client_order_id,
            strategy_name=request.strategy_id,
        )
        return 101

    def query_order(self, order_id):
        if self.query_ambiguous:
            raise BrokerQueryAmbiguous("query unavailable")
        return self.order

    def query_orders(self):
        if self.query_ambiguous:
            raise BrokerQueryAmbiguous("query unavailable")
        return () if self.order is None else (self.order,)

    def cancel_order(self, order_id):
        raise AssertionError("cancel not used in this test")


def _request() -> ExecutionRequest:
    return ExecutionRequest("c1", "0700.HK", Side.BUY, 100, 10.0, "s", "s-c1")


def _session(
    tmp_path: Path,
    broker: Broker,
    coordinator: SQLiteExecutionCoordinator,
) -> CoordinatedExecutionSession:
    return CoordinatedExecutionSession(
        broker=broker,
        guard=AllowGuard(),
        journal_path=tmp_path / "journal.json",
        lock_path=tmp_path / "session.lock",
        coordinator=coordinator,
        account_key="account-a",
        account_resource=broker,
        cash_estimator=ConservativeCashRequirementEstimator(safety_buffer=10.0),
        execution_id="s",
    )


def test_unknown_then_failed_restart_quarantines_and_retains_shared_resources(tmp_path: Path):
    coordinator = SQLiteExecutionCoordinator(tmp_path / "coord.db")
    broker = Broker()
    request = _request()

    first = _session(tmp_path, broker, coordinator)
    first.open()
    assert first.submit(request).state is TradeState.WORKING
    assert coordinator.active_reserved_cash("account-a") == 1010.0

    broker.query_ambiguous = True
    assert first.poll().state is TradeState.UNKNOWN
    claim = coordinator.get_claim("account-a", "0700.HK")
    assert claim is not None
    assert claim.finality is ExecutionFinality.OPEN
    first.close()

    restarted = _session(tmp_path, broker, coordinator)
    out = restarted.open()
    assert out.state is TradeState.FAILED
    claim = coordinator.get_claim("account-a", "0700.HK")
    assert claim is not None
    assert claim.finality is ExecutionFinality.QUARANTINED
    assert coordinator.active_reserved_cash("account-a") == 1010.0

    with pytest.raises(SymbolClaimConflict):
        coordinator.prepare(
            account_key="account-a",
            execution_id="other",
            request=ExecutionRequest(
                "c2", "0700.HK", Side.SELL, 100, 10.0, "other", "other-c2"
            ),
            broker_available_cash=None,
            required_cash=None,
        )
    restarted.close()


def test_authoritative_fill_releases_claim_and_reservation(tmp_path: Path):
    coordinator = SQLiteExecutionCoordinator(tmp_path / "coord.db")
    broker = Broker()
    request = _request()
    session = _session(tmp_path, broker, coordinator)
    session.open()
    assert session.submit(request).state is TradeState.WORKING

    broker.order = BrokerOrder(
        order_id=101,
        symbol=request.symbol,
        side=request.side,
        qty=request.qty,
        filled_qty=request.qty,
        status=BrokerOrderStatus.FILLED,
        order_remark=request.order_remark,
        client_order_id=request.client_order_id,
        strategy_name=request.strategy_id,
        average_fill_price=9.99,
    )
    assert session.poll().state is TradeState.FILLED
    assert coordinator.get_claim("account-a", "0700.HK") is None
    assert coordinator.active_reserved_cash("account-a") == 0.0
    reservation = coordinator.get_reservation("account-a", "s", "c1")
    assert reservation is not None
    assert reservation.active is False
    session.close()
