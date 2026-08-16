from __future__ import annotations

from pathlib import Path
from typing import Callable

from .domain import (
    BrokerOrder,
    BrokerOrderStatus,
    CancelRequestResult,
    ExecutionRequest,
    ExecutionSnapshot,
    Side,
    TradeEvent,
    TradeState,
)
from .exceptions import (
    BrokerError,
    BrokerQueryAmbiguous,
    BrokerSubmissionAmbiguous,
    BrokerSubmissionRejected,
    RecoveryAmbiguous,
    SessionClosedError,
)
from .journal import ExecutionJournal, JournalIntegrityError
from .mutex import ExecutionMutex
from .ports import BrokerPort, ExecutionGuard
from .recovery import event_for_observation, find_unique_managed_order
from .state_machine import MachineSnapshot, TRANSITIONS, advance
from .verifier import verify_state_machine


def _noop_submit(request: ExecutionRequest) -> None:
    return None


def _noop_cancel(order_id: int) -> None:
    return None


class ExecutionSession:
    """Reusable one-execution-at-a-time fail-closed session.

    The session owns its execution mutex for the whole open lifetime. Releasing
    the mutex via `close()` irreversibly disables further calls on this object.

    v0.4 preserves the existing ``before_broker_submit`` /
    ``before_broker_cancel`` sidecar API and adds an optional internal-style
    ``before_submit_coordination`` seam.  Submit ordering is:

        durable intent -> coordination -> project sidecar -> broker side effect

    All hooks execute synchronously on the calling thread.  If either pre-
    broker hook raises, broker.place_order has provably not been invoked; the
    logical execution is transitioned to REJECTED/RESOLVED before the original
    exception is propagated (or, for a BrokerSubmissionRejected coordination
    decision, returned as a normal REJECTED snapshot).
    """

    def __init__(
        self,
        *,
        broker: BrokerPort,
        guard: ExecutionGuard,
        journal_path: Path | str,
        lock_path: Path | str,
        execution_id: str = "default",
        before_submit_coordination: Callable[[ExecutionRequest], None] | None = None,
        before_broker_submit: Callable[[ExecutionRequest], None] | None = None,
        before_broker_cancel: Callable[[int], None] | None = None,
    ) -> None:
        if before_submit_coordination is not None and not callable(
            before_submit_coordination
        ):
            raise TypeError("before_submit_coordination must be callable or None")
        if before_broker_submit is not None and not callable(before_broker_submit):
            raise TypeError("before_broker_submit must be callable or None")
        if before_broker_cancel is not None and not callable(before_broker_cancel):
            raise TypeError("before_broker_cancel must be callable or None")
        self.broker = broker
        self.guard = guard
        self.journal = ExecutionJournal(journal_path, execution_id=execution_id)
        self.mutex = ExecutionMutex(lock_path)
        self._before_submit_coordination: Callable[[ExecutionRequest], None] = (
            before_submit_coordination or _noop_submit
        )
        self._before_broker_submit: Callable[[ExecutionRequest], None] = (
            before_broker_submit or _noop_submit
        )
        self._before_broker_cancel: Callable[[int], None] = (
            before_broker_cancel or _noop_cancel
        )
        self._snapshot = MachineSnapshot()
        self._open = False
        self._closed = False

    @property
    def machine(self) -> MachineSnapshot:
        return self._snapshot

    def open(self) -> ExecutionSnapshot:
        if self._closed:
            raise SessionClosedError("session object was closed and cannot be reopened")
        if self._open:
            return self.snapshot()

        self.mutex.acquire()
        try:
            self._snapshot, existed = self.journal.open()
            verification = verify_state_machine()
            spec_hash = str(verification["transition_spec_sha256"])
            source_hash = str(verification["execution_source_sha256"])
            bound = self.journal.data.get("formal_verification")
            if bound is None:
                self.journal.bind_verification(
                    transition_spec_sha256=spec_hash,
                    execution_source_sha256=source_hash,
                )
            elif not self.journal.verification_matches(
                transition_spec_sha256=spec_hash,
                execution_source_sha256=source_hash,
            ):
                raise JournalIntegrityError(
                    "journal is bound to a different execution state machine/source build"
                )
            session_evidence = self.guard.verify_session()
            session_evidence.validate()
            if not session_evidence.ready:
                raise RuntimeError(session_evidence.reason or "session verification failed")

            if not existed or self._snapshot.state is TradeState.IDLE:
                self._transition(
                    TradeEvent.SESSION_READY,
                    session_evidence=session_evidence,
                    details={"source": "session_guard"},
                )
            elif self._snapshot.state in {
                TradeState.WAIT_TRIGGER,
                TradeState.FILLED,
                TradeState.CANCELLED,
                TradeState.REJECTED,
                TradeState.FAILED,
            }:
                pass
            elif self._snapshot.state in {
                TradeState.SUBMITTED,
                TradeState.ACCEPTED,
                TradeState.WORKING,
                TradeState.PARTIALLY_FILLED,
                TradeState.PENDING_CANCEL,
                TradeState.CANCELLING,
                TradeState.CANCEL_REJECTED,
            }:
                self._transition(TradeEvent.RESTART_RECOVERY, details={"source": "restart"})
                self._recover_unknown()
            elif self._snapshot.state is TradeState.UNKNOWN:
                self._recover_unknown()
            else:
                self._transition(
                    TradeEvent.FATAL,
                    details={"reason": "interrupted pre-submit state"},
                )

            self._open = True
            return self.snapshot()
        except Exception:
            self.mutex.release()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self.mutex.release()
        self._open = False
        self._closed = True

    def submit(self, request: ExecutionRequest) -> ExecutionSnapshot:
        self._require_open()
        if self._snapshot.state is not TradeState.WAIT_TRIGGER:
            raise RuntimeError(f"cannot submit from {self._snapshot.state.value}")
        if not self.broker.execution_healthy():
            raise BrokerSubmissionRejected(
                "broker execution health is degraded; no intent was persisted"
            )
        self.journal.assert_identity_unused(request)

        self._transition(TradeEvent.TRIGGERED)
        self._transition(TradeEvent.BEGIN_PRECHECK)

        evidence = self.guard.verify(request)
        evidence.validate()
        if not evidence.allowed:
            self._transition(
                TradeEvent.PRECHECK_REJECTED,
                details={"reason": evidence.reason or "precheck rejected"},
            )
            return self.snapshot(reason=evidence.reason)

        self._transition(
            TradeEvent.PRECHECK_VERIFIED,
            precheck_evidence=evidence,
            details={"source": "execution_guard"},
        )

        self.journal.persist_intent(request)
        self._transition(
            TradeEvent.INTENT_PERSISTED,
            details={"client_order_id": request.client_order_id},
        )

        # v0.4 shared-account coordination runs after the generic durable
        # intent but before any project sidecar or broker side effect.  A
        # BrokerSubmissionRejected is a normal fail-closed local rejection;
        # any other exception is propagated after recording a resolved local
        # rejection.  In both cases broker.place_order was never called.
        try:
            self._before_submit_coordination(request)
        except BrokerSubmissionRejected as exc:
            self._transition(
                TradeEvent.SUBMIT_REJECTED,
                details={
                    "reason": str(exc),
                    "source": "before_submit_coordination",
                    "broker_invoked": False,
                },
            )
            return self.snapshot(reason=str(exc))
        except Exception as exc:
            self._transition(
                TradeEvent.SUBMIT_REJECTED,
                details={
                    "reason": str(exc),
                    "source": "before_submit_coordination",
                    "broker_invoked": False,
                },
            )
            raise

        # Project sidecar remains after core durable intent and before broker
        # submit. A synchronous failure proves the broker was not called, so
        # v0.4 records a resolved REJECTED execution before propagating it.
        try:
            self._before_broker_submit(request)
        except Exception as exc:
            self._transition(
                TradeEvent.SUBMIT_REJECTED,
                details={
                    "reason": str(exc),
                    "source": "before_broker_submit",
                    "broker_invoked": False,
                },
            )
            raise

        try:
            order_id = self.broker.place_order(request)
        except BrokerSubmissionRejected as exc:
            self._transition(TradeEvent.SUBMIT_REJECTED, details={"reason": str(exc)})
            return self.snapshot(reason=str(exc))
        except BrokerSubmissionAmbiguous as exc:
            self._transition(TradeEvent.SUBMIT_AMBIGUOUS, details={"reason": str(exc)})
            return self.snapshot(reason=str(exc))
        except BrokerError as exc:
            self._transition(TradeEvent.SUBMIT_AMBIGUOUS, details={"reason": str(exc)})
            return self.snapshot(reason=str(exc))

        self.journal.persist_broker_order_id(order_id)
        self._transition(TradeEvent.SUBMIT_ACCEPTED, details={"broker_order_id": order_id})
        return self.poll()

    def poll(self) -> ExecutionSnapshot:
        self._require_open()
        data = self.journal.data
        order_id = data.get("broker_order_id")
        if order_id is None:
            if self._snapshot.state is TradeState.UNKNOWN:
                return self._recover_unknown()
            raise RecoveryAmbiguous("no durable broker order id for poll")
        if type(order_id) is not int or order_id <= 0:
            raise JournalIntegrityError("invalid durable broker order id")
        try:
            order = self.broker.query_order(order_id)
        except BrokerQueryAmbiguous as exc:
            self._to_unknown(str(exc))
            return self.snapshot(reason=str(exc))
        return self._apply_observation(order)

    def reconcile(self) -> ExecutionSnapshot:
        self._require_open()
        if self._snapshot.state is TradeState.UNKNOWN:
            return self._recover_unknown()
        if self._snapshot.state in {
            TradeState.ACCEPTED,
            TradeState.WORKING,
            TradeState.PARTIALLY_FILLED,
            TradeState.PENDING_CANCEL,
            TradeState.CANCELLING,
            TradeState.CANCEL_REJECTED,
        }:
            return self.poll()
        return self.snapshot()

    def cancel(self) -> ExecutionSnapshot:
        self._require_open()
        if self._snapshot.state not in {
            TradeState.ACCEPTED,
            TradeState.WORKING,
            TradeState.PARTIALLY_FILLED,
        }:
            raise RuntimeError(f"cannot cancel from {self._snapshot.state.value}")
        order_id = self.journal.data.get("broker_order_id")
        if type(order_id) is not int or order_id <= 0:
            raise RecoveryAmbiguous("cancel requires a durable broker order id")

        self.journal.persist_cancel_intent()
        self._transition(TradeEvent.CANCEL_REQUESTED)
        self._before_broker_cancel(order_id)
        try:
            result = self.broker.cancel_order(order_id)
        except BrokerError:
            result = CancelRequestResult.REJECTED

        if result is CancelRequestResult.ACCEPTED:
            self._transition(TradeEvent.CANCEL_SENT)
        else:
            self._transition(TradeEvent.CANCEL_REQUEST_REJECTED)
        return self.poll()

    def next_cycle(self) -> ExecutionSnapshot:
        self._require_open()
        if TradeEvent.NEXT_CYCLE not in TRANSITIONS[self._snapshot.state]:
            raise RuntimeError(f"cannot start next cycle from {self._snapshot.state.value}")
        self._transition(TradeEvent.NEXT_CYCLE)
        self.journal.clear_cycle_data()
        return self.snapshot()

    def snapshot(self, *, reason: str = "") -> ExecutionSnapshot:
        data = self.journal.data if self.journal.is_open else {}
        intent = data.get("intent") if isinstance(data.get("intent"), dict) else {}
        observation = (
            data.get("last_observation")
            if isinstance(data.get("last_observation"), dict)
            else {}
        )
        order_id = data.get("broker_order_id")
        return ExecutionSnapshot(
            state=self._snapshot.state,
            client_order_id=intent.get("client_order_id") if intent else None,
            broker_order_id=order_id if type(order_id) is int else None,
            ordered_qty=int(intent.get("qty", 0)) if intent else 0,
            filled_qty=int(observation.get("filled_qty", 0)) if observation else 0,
            average_fill_price=(
                observation.get("average_fill_price") if observation else None
            ),
            reason=reason,
        )

    def _recover_unknown(self) -> ExecutionSnapshot:
        data = self.journal.data
        order_id = data.get("broker_order_id")
        try:
            if type(order_id) is int and order_id > 0:
                order = self.broker.query_order(order_id)
            else:
                intent = data.get("intent")
                if not isinstance(intent, dict):
                    raise RecoveryAmbiguous("UNKNOWN has no durable intent")
                orders = self.broker.query_orders()
                order = find_unique_managed_order(
                    orders,
                    symbol=str(intent["symbol"]),
                    side=Side(str(intent["side"])),
                    qty=int(intent["qty"]),
                    order_remark=str(intent["order_remark"]),
                )
                self.journal.persist_broker_order_id(order.order_id)
        except (
            BrokerQueryAmbiguous,
            RecoveryAmbiguous,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            self._transition(TradeEvent.RECOVERY_FAILED, details={"reason": str(exc)})
            return self.snapshot(reason=str(exc))
        return self._apply_observation(order)

    def _apply_observation(self, order: BrokerOrder) -> ExecutionSnapshot:
        self._validate_identity(order)
        previous = self.journal.data.get("last_observation")
        if isinstance(previous, dict):
            prev_filled = int(previous.get("filled_qty", 0))
            if order.filled_qty < prev_filled:
                self._transition(
                    TradeEvent.FATAL,
                    details={"reason": "broker filled quantity moved backwards"},
                )
                return self.snapshot(reason="broker filled quantity moved backwards")

        self.journal.update_observation(
            status=order.status.value,
            filled_qty=order.filled_qty,
            average_fill_price=order.average_fill_price,
        )
        try:
            event = event_for_observation(self._snapshot.state, order.status)
        except RecoveryAmbiguous as exc:
            self._to_unknown(str(exc))
            return self.snapshot(reason=str(exc))
        if event is not None:
            self._transition(
                event,
                details={
                    "broker_order_id": order.order_id,
                    "broker_status": order.status.value,
                    "filled_qty": order.filled_qty,
                },
            )
        return self.snapshot()

    def _validate_identity(self, order: BrokerOrder) -> None:
        data = self.journal.data
        intent = data.get("intent")
        if not isinstance(intent, dict):
            raise RecoveryAmbiguous("broker order exists without durable local intent")
        durable_order_id = data.get("broker_order_id")
        if type(durable_order_id) is int and order.order_id != durable_order_id:
            raise RecoveryAmbiguous(
                "broker order id conflicts with durable broker_order_id"
            )
        if (
            order.symbol != intent.get("symbol")
            or order.side.value != intent.get("side")
            or order.qty != intent.get("qty")
        ):
            raise RecoveryAmbiguous("broker order identity conflicts with durable intent")
        durable_remark = intent.get("order_remark")
        if order.order_remark and order.order_remark != durable_remark:
            raise RecoveryAmbiguous("broker order remark conflicts with durable intent")
        durable_strategy = intent.get("strategy_id")
        if order.strategy_name and order.strategy_name != durable_strategy:
            raise RecoveryAmbiguous("broker strategy_name conflicts with durable intent")

    def _to_unknown(self, reason: str) -> None:
        if self._snapshot.state is TradeState.UNKNOWN:
            self._transition(TradeEvent.QUERY_AMBIGUOUS, details={"reason": reason})
            return
        if TradeEvent.QUERY_AMBIGUOUS in TRANSITIONS[self._snapshot.state]:
            self._transition(TradeEvent.QUERY_AMBIGUOUS, details={"reason": reason})
            return
        self._transition(TradeEvent.FATAL, details={"reason": reason})

    def _transition(
        self,
        event: TradeEvent,
        *,
        details: dict | None = None,
        **kwargs,
    ) -> None:
        self._snapshot = advance(self._snapshot, event, **kwargs)
        self.journal.transition(event, self._snapshot, details=details)

    def _require_open(self) -> None:
        if not self._open or self._closed or not self.mutex.owned:
            raise SessionClosedError(
                "execution session is not open or no longer owns its mutex"
            )
