from __future__ import annotations

import multiprocessing as mp
from pathlib import Path

import pytest

from qmt_execution_core.coordination import (
    CashReservationRejected,
    ConservativeCashRequirementEstimator,
    SQLiteExecutionCoordinator,
)
from qmt_execution_core.domain import BrokerAsset, ExecutionRequest, SafetyFacts, Side, TradeState
from qmt_execution_core.exceptions import SymbolClaimConflict
from qmt_execution_core.finality import ExecutionFinality, execution_finality
from qmt_execution_core.state_machine import MachineSnapshot


def _request(client: str, symbol: str, *, side: Side = Side.BUY, price: float = 1.0):
    return ExecutionRequest(client, symbol, side, 10, price, "strategy", f"remark-{client}")


def _claim_worker(db_path: str, client: str, queue) -> None:
    coordinator = SQLiteExecutionCoordinator(db_path)
    request = _request(client, "510300.SH", side=Side.SELL)
    try:
        coordinator.prepare(
            account_key="account-a",
            execution_id=f"exec-{client}",
            request=request,
            broker_available_cash=None,
            required_cash=None,
        )
    except SymbolClaimConflict:
        queue.put("conflict")
    else:
        queue.put("ok")


def _cash_worker(db_path: str, client: str, symbol: str, amount: float, queue) -> None:
    coordinator = SQLiteExecutionCoordinator(db_path)
    request = _request(client, symbol)
    try:
        coordinator.prepare(
            account_key="account-a",
            execution_id=f"exec-{client}",
            request=request,
            broker_available_cash=100.0,
            required_cash=amount,
        )
    except CashReservationRejected:
        queue.put("cash-rejected")
    else:
        queue.put("ok")


def _two_process_results(target, args_a, args_b):
    context = mp.get_context("spawn")
    queue = context.Queue()
    first = context.Process(target=target, args=(*args_a, queue))
    second = context.Process(target=target, args=(*args_b, queue))
    first.start()
    second.start()
    first.join(10)
    second.join(10)
    assert first.exitcode == 0
    assert second.exitcode == 0
    return sorted([queue.get(timeout=2), queue.get(timeout=2)])


def test_execution_finality_keeps_unresolved_failed_quarantined():
    assert execution_finality(
        MachineSnapshot(TradeState.UNKNOWN, SafetyFacts(unresolved_order=True))
    ) is ExecutionFinality.OPEN
    assert execution_finality(
        MachineSnapshot(TradeState.CANCEL_REJECTED, SafetyFacts(unresolved_order=True))
    ) is ExecutionFinality.OPEN
    assert execution_finality(
        MachineSnapshot(TradeState.FAILED, SafetyFacts(unresolved_order=True))
    ) is ExecutionFinality.QUARANTINED
    assert execution_finality(
        MachineSnapshot(TradeState.FAILED, SafetyFacts(unresolved_order=False))
    ) is ExecutionFinality.RESOLVED
    assert execution_finality(MachineSnapshot(TradeState.FILLED)) is ExecutionFinality.RESOLVED


def test_same_symbol_cross_process_claim_allows_exactly_one(tmp_path: Path):
    db_path = str(tmp_path / "coord.db")
    SQLiteExecutionCoordinator(db_path)
    results = _two_process_results(
        _claim_worker,
        (db_path, "a"),
        (db_path, "b"),
    )
    assert results == ["conflict", "ok"]


def test_different_symbols_and_accounts_are_independent(tmp_path: Path):
    coordinator = SQLiteExecutionCoordinator(tmp_path / "coord.db")
    a = _request("a", "0700.HK", side=Side.SELL)
    b = _request("b", "510300.SH", side=Side.SELL)
    c = _request("c", "0700.HK", side=Side.SELL)

    coordinator.prepare(
        account_key="account-a",
        execution_id="exec-a",
        request=a,
        broker_available_cash=None,
        required_cash=None,
    )
    coordinator.prepare(
        account_key="account-a",
        execution_id="exec-b",
        request=b,
        broker_available_cash=None,
        required_cash=None,
    )
    coordinator.prepare(
        account_key="account-b",
        execution_id="exec-c",
        request=c,
        broker_available_cash=None,
        required_cash=None,
    )

    assert coordinator.get_claim("account-a", "0700.HK") is not None
    assert coordinator.get_claim("account-a", "510300.SH") is not None
    assert coordinator.get_claim("account-b", "0700.HK") is not None


def test_shared_cash_cross_process_race_cannot_overcommit(tmp_path: Path):
    db_path = str(tmp_path / "coord.db")
    SQLiteExecutionCoordinator(db_path)
    results = _two_process_results(
        _cash_worker,
        (db_path, "a", "0700.HK", 60.0),
        (db_path, "b", "510300.SH", 50.0),
    )
    assert results == ["cash-rejected", "ok"]


def test_conservative_estimator_includes_configured_buffers():
    estimator = ConservativeCashRequirementEstimator(
        fee_rate=0.001,
        minimum_fee=5.0,
        temporary_withholding_buffer=20.0,
        fx_rounding_rate=0.002,
        safety_buffer=10.0,
    )
    request = ExecutionRequest("c1", "0700.HK", Side.BUY, 100, 10.0, "s", "r1")
    asset = BrokerAsset(100000.0, 0.0, 0.0, 100000.0)
    estimate = estimator.estimate(request, asset)
    assert estimate.order_notional == 1000.0
    assert estimate.required_cash > estimate.order_notional
    assert estimate.transaction_cost_buffer == 5.0
    assert estimate.temporary_withholding_buffer == 20.0


def test_resolved_execution_releases_claim_and_reservation(tmp_path: Path):
    coordinator = SQLiteExecutionCoordinator(tmp_path / "coord.db")
    request = _request("a", "0700.HK")
    coordinator.prepare(
        account_key="account-a",
        execution_id="exec-a",
        request=request,
        broker_available_cash=100.0,
        required_cash=60.0,
    )
    assert coordinator.active_reserved_cash("account-a") == 60.0

    coordinator.update_finality(
        account_key="account-a",
        execution_id="exec-a",
        request=request,
        finality=ExecutionFinality.RESOLVED,
    )
    assert coordinator.get_claim("account-a", "0700.HK") is None
    reservation = coordinator.get_reservation("account-a", "exec-a", "a")
    assert reservation is not None
    assert reservation.active is False
    assert coordinator.active_reserved_cash("account-a") == 0.0


def test_released_reservation_is_not_local_cash_credit(tmp_path: Path):
    coordinator = SQLiteExecutionCoordinator(tmp_path / "coord.db")
    first = _request("a", "0700.HK")
    coordinator.prepare(
        account_key="account-a",
        execution_id="exec-a",
        request=first,
        broker_available_cash=100.0,
        required_cash=60.0,
    )
    coordinator.update_finality(
        account_key="account-a",
        execution_id="exec-a",
        request=first,
        finality=ExecutionFinality.RESOLVED,
    )

    # The next order must use the new broker snapshot (40), not 40 + the
    # locally released 60 reservation.
    second = _request("b", "510300.SH")
    with pytest.raises(CashReservationRejected):
        coordinator.prepare(
            account_key="account-a",
            execution_id="exec-b",
            request=second,
            broker_available_cash=40.0,
            required_cash=50.0,
        )


def test_quarantined_restore_holds_symbol_and_cash_without_rechecking_available(tmp_path: Path):
    coordinator = SQLiteExecutionCoordinator(tmp_path / "coord.db")
    request = _request("a", "0700.HK")
    coordinator.restore(
        account_key="account-a",
        execution_id="exec-a",
        request=request,
        required_cash=60.0,
        finality=ExecutionFinality.QUARANTINED,
    )
    claim = coordinator.get_claim("account-a", "0700.HK")
    assert claim is not None
    assert claim.finality is ExecutionFinality.QUARANTINED
    assert coordinator.active_reserved_cash("account-a") == 60.0

    with pytest.raises(SymbolClaimConflict):
        coordinator.prepare(
            account_key="account-a",
            execution_id="exec-b",
            request=_request("b", "0700.HK", side=Side.SELL),
            broker_available_cash=None,
            required_cash=None,
        )
