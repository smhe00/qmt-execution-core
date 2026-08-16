from __future__ import annotations

from pathlib import Path
from typing import Callable

from .coordination import CashRequirementEstimator, ExecutionCoordinator
from .domain import (
    BrokerOrder,
    CancelRequestResult,
    ExecutionRequest,
    ExecutionSnapshot,
    Side,
)
from .exceptions import (
    BrokerError,
    BrokerSubmissionAmbiguous,
    BrokerSubmissionRejected,
    CoordinationError,
    SymbolClaimConflict,
)
from .finality import ExecutionFinality, execution_finality
from .ports import AccountResourcePort, BrokerPort, ExecutionGuard
from .session import ExecutionSession


class _CoordinatedBrokerPort:
    """Broker wrapper that coordinates shared resources before submit.

    The wrapped ExecutionSession still owns the reliable order lifecycle. This
    wrapper only inserts the v0.4 shared-account critical section immediately
    before the real BrokerPort.place_order side effect.
    """

    def __init__(
        self,
        *,
        broker: BrokerPort,
        coordinator: ExecutionCoordinator,
        account_key: str,
        execution_id: str,
        account_resource: AccountResourcePort,
        cash_estimator: CashRequirementEstimator | None,
    ) -> None:
        self.raw_broker = broker
        self.coordinator = coordinator
        self.account_key = account_key
        self.execution_id = execution_id
        self.account_resource = account_resource
        self.cash_estimator = cash_estimator

    def place_order(self, request: ExecutionRequest) -> int:
        broker_available_cash: float | None = None
        required_cash: float | None = None

        if request.side is Side.BUY:
            if self.cash_estimator is None:
                raise BrokerSubmissionRejected(
                    "coordinated BUY requires an explicit CashRequirementEstimator; "
                    "broker order was not invoked"
                )
            try:
                asset = self.account_resource.query_asset()
                estimate = self.cash_estimator.estimate(request, asset)
                broker_available_cash = float(asset.cash)
                required_cash = float(estimate.required_cash)
            except BrokerSubmissionRejected:
                raise
            except Exception as exc:
                raise BrokerSubmissionRejected(
                    "fresh authoritative cash/requirement estimation failed; "
                    "broker order was not invoked"
                ) from exc

        try:
            self.coordinator.prepare(
                account_key=self.account_key,
                execution_id=self.execution_id,
                request=request,
                broker_available_cash=broker_available_cash,
                required_cash=required_cash,
            )
        except CoordinationError as exc:
            raise BrokerSubmissionRejected(
                f"shared-account coordination rejected submit before broker side effect: {exc}"
            ) from exc

        try:
            return self.raw_broker.place_order(request)
        except BrokerSubmissionRejected:
            # Broker/API definitively says no order exists. Releasing the
            # local claim/reservation is safe; a later order still refreshes
            # broker cash rather than adding this reservation back locally.
            self.coordinator.update_finality(
                account_key=self.account_key,
                execution_id=self.execution_id,
                request=request,
                finality=ExecutionFinality.RESOLVED,
            )
            raise
        except (BrokerSubmissionAmbiguous, BrokerError):
            # Outcome may exist at the broker: retain claim + cash reservation.
            raise
        except Exception as exc:
            # A generic broker implementation violated the BrokerPort error
            # contract. Treat it as ambiguous, never as permission to release
            # resources or blindly resend.
            raise BrokerSubmissionAmbiguous(
                "coordinated broker submit raised unexpectedly; outcome unknown"
            ) from exc

    def cancel_order(self, order_id: int) -> CancelRequestResult:
        return self.raw_broker.cancel_order(order_id)

    def query_order(self, order_id: int) -> BrokerOrder:
        return self.raw_broker.query_order(order_id)

    def query_orders(self) -> tuple[BrokerOrder, ...]:
        return self.raw_broker.query_orders()

    def execution_healthy(self) -> bool:
        return self.raw_broker.execution_healthy()

    def __getattr__(self, name: str):
        # Preserve access to optional broker-specific read-only query surfaces
        # (query_asset/query_positions/query_trades) without widening BrokerPort.
        return getattr(self.raw_broker, name)


class CoordinatedExecutionSession(ExecutionSession):
    """ExecutionSession with durable per-symbol and shared-cash coordination.

    It remains one-active-execution-at-a-time. Cross-symbol concurrency comes
    from multiple independent instances/processes sharing the same coordinator.
    """

    def __init__(
        self,
        *,
        broker: BrokerPort,
        guard: ExecutionGuard,
        journal_path: Path | str,
        lock_path: Path | str,
        coordinator: ExecutionCoordinator,
        account_key: str,
        account_resource: AccountResourcePort,
        cash_estimator: CashRequirementEstimator | None,
        execution_id: str = "default",
        before_broker_submit: Callable[[ExecutionRequest], None] | None = None,
        before_broker_cancel: Callable[[int], None] | None = None,
    ) -> None:
        if type(account_key) is not str or not account_key:
            raise ValueError("account_key must be a non-empty string")
        if type(execution_id) is not str or not execution_id:
            raise ValueError("execution_id must be a non-empty string")
        self.coordinator = coordinator
        self.account_key = account_key
        self.account_resource = account_resource
        self.cash_estimator = cash_estimator
        self.execution_id = execution_id
        self.raw_broker = broker
        coordinated_broker = _CoordinatedBrokerPort(
            broker=broker,
            coordinator=coordinator,
            account_key=account_key,
            execution_id=execution_id,
            account_resource=account_resource,
            cash_estimator=cash_estimator,
        )
        super().__init__(
            broker=coordinated_broker,
            guard=guard,
            journal_path=journal_path,
            lock_path=lock_path,
            execution_id=execution_id,
            before_broker_submit=before_broker_submit,
            before_broker_cancel=before_broker_cancel,
        )

    def open(self) -> ExecutionSnapshot:
        return self._sync(super().open())

    def submit(self, request: ExecutionRequest) -> ExecutionSnapshot:
        return self._sync(super().submit(request))

    def poll(self) -> ExecutionSnapshot:
        return self._sync(super().poll())

    def reconcile(self) -> ExecutionSnapshot:
        return self._sync(super().reconcile())

    def cancel(self) -> ExecutionSnapshot:
        return self._sync(super().cancel())

    def next_cycle(self) -> ExecutionSnapshot:
        # A previous resolved transition has already released shared resources.
        return super().next_cycle()

    def _durable_request(self) -> ExecutionRequest | None:
        if not self.journal.is_open:
            return None
        intent = self.journal.data.get("intent")
        if intent is None:
            return None
        if not isinstance(intent, dict):
            raise CoordinationError("durable intent is malformed for coordination")
        try:
            return ExecutionRequest(
                client_order_id=str(intent["client_order_id"]),
                symbol=str(intent["symbol"]),
                side=str(intent["side"]),
                qty=int(intent["qty"]),
                limit_price=float(intent["limit_price"]),
                strategy_id=str(intent["strategy_id"]),
                order_remark=str(intent["order_remark"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CoordinationError(
                "durable intent cannot be reconstructed for coordination"
            ) from exc

    def _required_cash_for_restore(self, request: ExecutionRequest) -> float | None:
        if request.side is not Side.BUY:
            return None
        if self.cash_estimator is None:
            raise CoordinationError(
                "cannot restore coordinated BUY without CashRequirementEstimator"
            )
        try:
            asset = self.account_resource.query_asset()
            estimate = self.cash_estimator.estimate(request, asset)
        except Exception as exc:
            raise CoordinationError(
                "cannot restore coordinated BUY without authoritative account facts"
            ) from exc
        return float(estimate.required_cash)

    def _sync(self, snapshot: ExecutionSnapshot) -> ExecutionSnapshot:
        request = self._durable_request()
        if request is None:
            return snapshot
        finality = execution_finality(self.machine)

        if finality is ExecutionFinality.RESOLVED:
            # A submit may have been rejected before this execution ever
            # acquired the symbol (for example another process already owns
            # it). Never update/delete another execution's claim.
            try:
                owns_claim = self.coordinator.has_claim(
                    account_key=self.account_key,
                    execution_id=self.execution_id,
                    request=request,
                )
            except SymbolClaimConflict:
                return snapshot
            if owns_claim:
                self.coordinator.update_finality(
                    account_key=self.account_key,
                    execution_id=self.execution_id,
                    request=request,
                    finality=finality,
                )
            return snapshot

        if self.coordinator.has_claim(
            account_key=self.account_key,
            execution_id=self.execution_id,
            request=request,
        ):
            self.coordinator.update_finality(
                account_key=self.account_key,
                execution_id=self.execution_id,
                request=request,
                finality=finality,
            )
            return snapshot

        # Recovery path: if the coordination DB was recreated or the durable
        # claim is otherwise missing, re-establish it conservatively. Existing
        # broker orders do not re-check available cash because that cash may
        # already be frozen/reflected by the broker.
        self.coordinator.restore(
            account_key=self.account_key,
            execution_id=self.execution_id,
            request=request,
            required_cash=self._required_cash_for_restore(request),
            finality=finality,
        )
        return snapshot
