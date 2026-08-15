from __future__ import annotations

import hashlib
from collections import deque
from pathlib import Path

from .domain import PrecheckEvidence, SessionEvidence, TradeEvent, TradeState
from .state_machine import (
    InvalidTransition,
    MachineSnapshot,
    TERMINAL_STATES,
    TRANSITIONS,
    advance,
    assert_invariants,
    initial_snapshot,
)


PROTECTED_EXECUTION_SOURCES = (
    "src/qmt_execution_core/domain.py",
    "src/qmt_execution_core/exceptions.py",
    "src/qmt_execution_core/state_machine.py",
    "src/qmt_execution_core/ports.py",
    "src/qmt_execution_core/journal.py",
    "src/qmt_execution_core/mutex.py",
    "src/qmt_execution_core/recovery.py",
    "src/qmt_execution_core/session.py",
    "src/qmt_execution_core/verifier.py",
    "src/qmt_execution_core/miniqmt/status.py",
    "src/qmt_execution_core/miniqmt/adapter.py",
    "src/qmt_execution_core/miniqmt/callbacks.py",
)


def _canonical_session_evidence() -> SessionEvidence:
    return SessionEvidence(
        ready=True,
        environment_verified=True,
        account_verified=True,
    )


def _canonical_precheck_evidence() -> PrecheckEvidence:
    return PrecheckEvidence(
        allowed=True,
        environment_verified=True,
        account_verified=True,
        broker_snapshot_verified=True,
        position_verified=True,
        cash_verified=True,
        quote_verified=True,
    )


def _step(snapshot: MachineSnapshot, event: TradeEvent) -> MachineSnapshot:
    kwargs = {}
    if event is TradeEvent.SESSION_READY:
        kwargs["session_evidence"] = _canonical_session_evidence()
    elif event is TradeEvent.PRECHECK_VERIFIED:
        kwargs["precheck_evidence"] = _canonical_precheck_evidence()
    return advance(snapshot, event, **kwargs)


def verify_state_machine(*, source_root: Path | None = None) -> dict[str, object]:
    initial = initial_snapshot()
    queue = deque([initial])
    reachable = {initial}
    edges: set[tuple[MachineSnapshot, TradeEvent, MachineSnapshot]] = set()
    reached_declared_edges: set[tuple[TradeState, TradeEvent]] = set()

    while queue:
        current = queue.popleft()
        assert_invariants(current)
        for event in TRANSITIONS[current.state]:
            try:
                successor = _step(current, event)
            except InvalidTransition:
                continue
            assert_invariants(successor)
            edges.add((current, event, successor))
            reached_declared_edges.add((current.state, event))
            if successor not in reachable:
                reachable.add(successor)
                queue.append(successor)

    declared_edges = {
        (state, event)
        for state, event_map in TRANSITIONS.items()
        for event in event_map
    }
    missing_edges = declared_edges - reached_declared_edges
    missing_states = set(TRANSITIONS) - {snapshot.state for snapshot in reachable}
    if missing_edges:
        raise RuntimeError(f"unreachable declared transitions: {sorted((s.value, e.value) for s, e in missing_edges)}")
    if missing_states:
        raise RuntimeError(f"unreachable states: {sorted(s.value for s in missing_states)}")

    reverse: dict[MachineSnapshot, set[MachineSnapshot]] = {s: set() for s in reachable}
    for src, _, dst in edges:
        reverse[dst].add(src)
    can_reach_terminal = {s for s in reachable if s.state in TERMINAL_STATES}
    frontier = deque(can_reach_terminal)
    while frontier:
        dst = frontier.popleft()
        for src in reverse[dst]:
            if src not in can_reach_terminal:
                can_reach_terminal.add(src)
                frontier.append(src)
    nonterminating = reachable - can_reach_terminal
    if nonterminating:
        raise RuntimeError("reachable abstract states exist with no terminal path")

    illegal_unknown_events = {
        TradeEvent.INTENT_PERSISTED,
        TradeEvent.SUBMIT_ACCEPTED,
        TradeEvent.TRIGGERED,
        TradeEvent.BEGIN_PRECHECK,
    } & set(TRANSITIONS[TradeState.UNKNOWN])
    if illegal_unknown_events:
        raise RuntimeError("UNKNOWN contains blind-retry/new-order transitions")

    return {
        "method": "exhaustive explicit-state reachability to fixed point",
        "reachable_abstract_states": len(reachable),
        "reachable_transitions": len(edges),
        "declared_states": len(TRANSITIONS),
        "declared_transitions": len(declared_edges),
        "unreachable_states": 0,
        "unreachable_transitions": 0,
        "states_without_terminal_path": 0,
        "invariant_violations": 0,
        "transition_spec_sha256": transition_spec_sha256(),
        "execution_source_sha256": execution_source_sha256(source_root),
    }


def transition_spec_sha256() -> str:
    rows: list[str] = []
    for state in sorted(TRANSITIONS, key=lambda s: s.value):
        for event, target in sorted(TRANSITIONS[state].items(), key=lambda item: item[0].value):
            rows.append(f"{state.value}|{event.value}|{target.value}\n")
    return hashlib.sha256("".join(rows).encode("utf-8")).hexdigest()


def execution_source_sha256(source_root: Path | None = None) -> str:
    root = source_root or Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for relative in PROTECTED_EXECUTION_SOURCES:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"protected execution source missing: {relative}")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest()
