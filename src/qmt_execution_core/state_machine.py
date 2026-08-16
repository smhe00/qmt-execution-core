from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from .domain import (
    PrecheckEvidence,
    SafetyFacts,
    SessionEvidence,
    TradeEvent,
    TradeState,
)


class InvalidTransition(RuntimeError):
    pass


class InvariantViolation(RuntimeError):
    pass


@dataclass(frozen=True)
class MachineSnapshot:
    state: TradeState = TradeState.IDLE
    facts: SafetyFacts = SafetyFacts()


TRANSITIONS: Mapping[TradeState, Mapping[TradeEvent, TradeState]] = {
    TradeState.IDLE: {
        TradeEvent.SESSION_READY: TradeState.WAIT_TRIGGER,
        TradeEvent.FATAL: TradeState.FAILED,
    },
    TradeState.WAIT_TRIGGER: {
        TradeEvent.TRIGGERED: TradeState.TRIGGER,
        TradeEvent.FATAL: TradeState.FAILED,
    },
    TradeState.TRIGGER: {
        TradeEvent.BEGIN_PRECHECK: TradeState.PRE_CHECK,
        TradeEvent.FATAL: TradeState.FAILED,
    },
    TradeState.PRE_CHECK: {
        TradeEvent.PRECHECK_VERIFIED: TradeState.PRE_CHECK,
        TradeEvent.PRECHECK_REJECTED: TradeState.REJECTED,
        TradeEvent.INTENT_PERSISTED: TradeState.SUBMITTED,
        TradeEvent.FATAL: TradeState.FAILED,
    },
    TradeState.SUBMITTED: {
        TradeEvent.PRE_BROKER_ABORTED: TradeState.FAILED,
        TradeEvent.SUBMIT_ACCEPTED: TradeState.ACCEPTED,
        TradeEvent.SUBMIT_REJECTED: TradeState.REJECTED,
        TradeEvent.SUBMIT_AMBIGUOUS: TradeState.UNKNOWN,
        TradeEvent.RESTART_RECOVERY: TradeState.UNKNOWN,
        TradeEvent.FATAL: TradeState.FAILED,
    },
    TradeState.ACCEPTED: {
        TradeEvent.ORDER_WORKING: TradeState.WORKING,
        TradeEvent.ORDER_PARTIAL: TradeState.PARTIALLY_FILLED,
        TradeEvent.ORDER_FILLED: TradeState.FILLED,
        TradeEvent.ORDER_REJECTED: TradeState.REJECTED,
        TradeEvent.CANCEL_REQUESTED: TradeState.PENDING_CANCEL,
        TradeEvent.CANCEL_CONFIRMED: TradeState.CANCELLED,
        TradeEvent.QUERY_AMBIGUOUS: TradeState.UNKNOWN,
        TradeEvent.RESTART_RECOVERY: TradeState.UNKNOWN,
        TradeEvent.FATAL: TradeState.FAILED,
    },
    TradeState.WORKING: {
        TradeEvent.ORDER_WORKING: TradeState.WORKING,
        TradeEvent.ORDER_PARTIAL: TradeState.PARTIALLY_FILLED,
        TradeEvent.ORDER_FILLED: TradeState.FILLED,
        TradeEvent.ORDER_REJECTED: TradeState.REJECTED,
        TradeEvent.CANCEL_REQUESTED: TradeState.PENDING_CANCEL,
        TradeEvent.CANCEL_CONFIRMED: TradeState.CANCELLED,
        TradeEvent.QUERY_AMBIGUOUS: TradeState.UNKNOWN,
        TradeEvent.RESTART_RECOVERY: TradeState.UNKNOWN,
        TradeEvent.FATAL: TradeState.FAILED,
    },
    TradeState.PARTIALLY_FILLED: {
        TradeEvent.ORDER_PARTIAL: TradeState.PARTIALLY_FILLED,
        TradeEvent.ORDER_FILLED: TradeState.FILLED,
        TradeEvent.CANCEL_REQUESTED: TradeState.PENDING_CANCEL,
        TradeEvent.CANCEL_CONFIRMED: TradeState.CANCELLED,
        TradeEvent.QUERY_AMBIGUOUS: TradeState.UNKNOWN,
        TradeEvent.RESTART_RECOVERY: TradeState.UNKNOWN,
        TradeEvent.FATAL: TradeState.FAILED,
    },
    TradeState.PENDING_CANCEL: {
        TradeEvent.CANCEL_SENT: TradeState.CANCELLING,
        TradeEvent.CANCEL_REQUEST_REJECTED: TradeState.CANCEL_REJECTED,
        TradeEvent.ORDER_WORKING: TradeState.WORKING,
        TradeEvent.ORDER_PARTIAL: TradeState.PARTIALLY_FILLED,
        TradeEvent.CANCEL_CONFIRMED: TradeState.CANCELLED,
        TradeEvent.ORDER_FILLED: TradeState.FILLED,
        TradeEvent.QUERY_AMBIGUOUS: TradeState.UNKNOWN,
        TradeEvent.RESTART_RECOVERY: TradeState.UNKNOWN,
        TradeEvent.FATAL: TradeState.FAILED,
    },
    TradeState.CANCELLING: {
        TradeEvent.CANCEL_STILL_PENDING: TradeState.CANCELLING,
        TradeEvent.CANCEL_CONFIRMED: TradeState.CANCELLED,
        TradeEvent.ORDER_WORKING: TradeState.WORKING,
        TradeEvent.ORDER_PARTIAL: TradeState.PARTIALLY_FILLED,
        TradeEvent.ORDER_FILLED: TradeState.FILLED,
        TradeEvent.QUERY_AMBIGUOUS: TradeState.UNKNOWN,
        TradeEvent.RESTART_RECOVERY: TradeState.UNKNOWN,
        TradeEvent.FATAL: TradeState.FAILED,
    },
    TradeState.CANCEL_REJECTED: {
        TradeEvent.RECOVERY_WORKING: TradeState.WORKING,
        TradeEvent.RECOVERY_PARTIAL: TradeState.PARTIALLY_FILLED,
        TradeEvent.RECOVERY_CANCELLING: TradeState.CANCELLING,
        TradeEvent.RECOVERY_FILLED: TradeState.FILLED,
        TradeEvent.RECOVERY_CANCELLED: TradeState.CANCELLED,
        TradeEvent.RECOVERY_REJECTED: TradeState.REJECTED,
        TradeEvent.RECOVERY_FAILED: TradeState.FAILED,
        TradeEvent.QUERY_AMBIGUOUS: TradeState.UNKNOWN,
        TradeEvent.RESTART_RECOVERY: TradeState.UNKNOWN,
    },
    TradeState.UNKNOWN: {
        TradeEvent.RECOVERY_ACCEPTED: TradeState.ACCEPTED,
        TradeEvent.RECOVERY_WORKING: TradeState.WORKING,
        TradeEvent.RECOVERY_PARTIAL: TradeState.PARTIALLY_FILLED,
        TradeEvent.RECOVERY_CANCELLING: TradeState.CANCELLING,
        TradeEvent.RECOVERY_FILLED: TradeState.FILLED,
        TradeEvent.RECOVERY_CANCELLED: TradeState.CANCELLED,
        TradeEvent.RECOVERY_REJECTED: TradeState.REJECTED,
        TradeEvent.RECOVERY_FAILED: TradeState.FAILED,
        TradeEvent.QUERY_AMBIGUOUS: TradeState.UNKNOWN,
    },
    TradeState.FILLED: {
        TradeEvent.NEXT_CYCLE: TradeState.WAIT_TRIGGER,
    },
    TradeState.CANCELLED: {
        TradeEvent.NEXT_CYCLE: TradeState.WAIT_TRIGGER,
    },
    TradeState.REJECTED: {
        TradeEvent.NEXT_CYCLE: TradeState.WAIT_TRIGGER,
    },
    TradeState.FAILED: {},
}

TERMINAL_STATES = {
    TradeState.FILLED,
    TradeState.CANCELLED,
    TradeState.REJECTED,
    TradeState.FAILED,
}


def initial_snapshot() -> MachineSnapshot:
    return MachineSnapshot()


def advance(
    snapshot: MachineSnapshot,
    event: TradeEvent,
    *,
    session_evidence: SessionEvidence | None = None,
    precheck_evidence: PrecheckEvidence | None = None,
) -> MachineSnapshot:
    try:
        next_state = TRANSITIONS[snapshot.state][event]
    except KeyError as exc:
        raise InvalidTransition(
            f"event {event.value!r} invalid from {snapshot.state.value!r}"
        ) from exc

    facts = snapshot.facts

    if event is TradeEvent.RECOVERY_CANCELLING and not facts.cancel_intent_persisted:
        raise InvalidTransition("cannot recover CANCEL_PENDING without durable cancel intent")
    if event is TradeEvent.INTENT_PERSISTED and not (
        facts.broker_snapshot_verified
        and facts.position_verified
        and facts.cash_verified
        and facts.quote_verified
    ):
        raise InvalidTransition("INTENT_PERSISTED requires a verified current-cycle precheck")

    if event is TradeEvent.SESSION_READY:
        if session_evidence is None:
            raise InvariantViolation("SESSION_READY requires SessionEvidence")
        session_evidence.validate()
        if not session_evidence.ready:
            raise InvariantViolation("SESSION_READY requires ready evidence")
        facts = replace(
            facts,
            environment_verified=session_evidence.environment_verified,
            account_verified=session_evidence.account_verified,
        )

    elif event is TradeEvent.PRECHECK_VERIFIED:
        if precheck_evidence is None:
            raise InvariantViolation("PRECHECK_VERIFIED requires PrecheckEvidence")
        precheck_evidence.validate()
        if not precheck_evidence.allowed:
            raise InvariantViolation("PRECHECK_VERIFIED requires allowed evidence")
        facts = replace(
            facts,
            environment_verified=precheck_evidence.environment_verified,
            account_verified=precheck_evidence.account_verified,
            broker_snapshot_verified=precheck_evidence.broker_snapshot_verified,
            position_verified=precheck_evidence.position_verified,
            cash_verified=precheck_evidence.cash_verified,
            quote_verified=precheck_evidence.quote_verified,
        )

    elif event is TradeEvent.INTENT_PERSISTED:
        facts = replace(
            facts,
            intent_persisted=True,
            reservation_persisted=True,
        )

    elif event is TradeEvent.PRE_BROKER_ABORTED:
        # The synchronous pre-broker hook failed before BrokerPort.place_order
        # was invoked. The durable logical execution failed, but broker reality
        # is resolved: no external order can exist from this attempt.
        facts = replace(
            facts,
            submitted_once=False,
            unresolved_order=False,
            terminal_order_confirmed=True,
        )

    elif event is TradeEvent.SUBMIT_ACCEPTED:
        facts = replace(facts, submitted_once=True, unresolved_order=True)

    elif event is TradeEvent.SUBMIT_AMBIGUOUS:
        facts = replace(facts, submitted_once=True, unresolved_order=True)

    elif event is TradeEvent.SUBMIT_REJECTED:
        facts = replace(
            facts,
            submitted_once=True,
            unresolved_order=False,
            terminal_order_confirmed=True,
        )

    elif event in {
        TradeEvent.ORDER_WORKING,
        TradeEvent.ORDER_PARTIAL,
        TradeEvent.RECOVERY_ACCEPTED,
        TradeEvent.RECOVERY_WORKING,
        TradeEvent.RECOVERY_PARTIAL,
        TradeEvent.RECOVERY_CANCELLING,
        TradeEvent.CANCEL_STILL_PENDING,
    }:
        facts = replace(facts, submitted_once=True, unresolved_order=True)

    elif event is TradeEvent.CANCEL_REQUESTED:
        facts = replace(facts, cancel_intent_persisted=True, unresolved_order=True)

    elif event is TradeEvent.CANCEL_SENT:
        facts = replace(facts, cancel_intent_persisted=True, unresolved_order=True)

    elif event is TradeEvent.CANCEL_REQUEST_REJECTED:
        facts = replace(facts, cancel_intent_persisted=True, unresolved_order=True)

    elif event in {
        TradeEvent.ORDER_FILLED,
        TradeEvent.CANCEL_CONFIRMED,
        TradeEvent.RECOVERY_FILLED,
        TradeEvent.RECOVERY_CANCELLED,
        TradeEvent.RECOVERY_REJECTED,
        TradeEvent.ORDER_REJECTED,
    }:
        facts = replace(
            facts,
            submitted_once=True,
            unresolved_order=False,
            terminal_order_confirmed=True,
        )

    elif event in {TradeEvent.QUERY_AMBIGUOUS, TradeEvent.RESTART_RECOVERY}:
        possibly_sent = facts.submitted_once or facts.intent_persisted
        facts = replace(
            facts,
            broker_snapshot_verified=False,
            cash_verified=False,
            quote_verified=False,
            unresolved_order=facts.unresolved_order or possibly_sent,
            terminal_order_confirmed=False,
        )

    elif event is TradeEvent.RECOVERY_FAILED:
        facts = replace(facts, unresolved_order=True)

    elif event is TradeEvent.NEXT_CYCLE:
        facts = SafetyFacts(
            environment_verified=facts.environment_verified,
            account_verified=facts.account_verified,
        )

    elif event is TradeEvent.FATAL:
        pass

    result = MachineSnapshot(next_state, facts)
    assert_invariants(result)
    return result


def assert_invariants(snapshot: MachineSnapshot) -> None:
    facts = snapshot.facts

    if facts.submitted_once and not (
        facts.intent_persisted and facts.reservation_persisted
    ):
        raise InvariantViolation("broker submission lacks durable intent/reservation")

    if snapshot.state in {
        TradeState.SUBMITTED,
        TradeState.ACCEPTED,
        TradeState.WORKING,
        TradeState.UNKNOWN,
        TradeState.PARTIALLY_FILLED,
        TradeState.PENDING_CANCEL,
        TradeState.CANCELLING,
        TradeState.CANCEL_REJECTED,
        TradeState.FILLED,
        TradeState.CANCELLED,
    }:
        if not (facts.environment_verified and facts.account_verified):
            raise InvariantViolation("execution path lacks environment/account verification")

    if snapshot.state is TradeState.SUBMITTED:
        if not (
            facts.broker_snapshot_verified
            and facts.position_verified
            and facts.cash_verified
            and facts.quote_verified
            and facts.intent_persisted
            and facts.reservation_persisted
        ):
            raise InvariantViolation("SUBMITTED lacks verified precheck or durable intent")

    if snapshot.state in {
        TradeState.WAIT_TRIGGER,
        TradeState.TRIGGER,
        TradeState.PRE_CHECK,
    } and facts.unresolved_order:
        raise InvariantViolation("unresolved order reached new-order path")

    if snapshot.state in {
        TradeState.PENDING_CANCEL,
        TradeState.CANCELLING,
        TradeState.CANCEL_REJECTED,
    } and not facts.cancel_intent_persisted:
        raise InvariantViolation("cancel path lacks durable cancel intent")

    if snapshot.state in {TradeState.FILLED, TradeState.CANCELLED}:
        if facts.unresolved_order or not facts.terminal_order_confirmed:
            raise InvariantViolation("successful terminal state lacks broker confirmation")

    if snapshot.state is TradeState.UNKNOWN and not facts.unresolved_order:
        raise InvariantViolation("UNKNOWN must preserve unresolved-order evidence")
