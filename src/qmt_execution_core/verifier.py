from __future__ import annotations

import hashlib
from collections import deque
from pathlib import Path

from .domain import PrecheckEvidence, SessionEvidence, TradeEvent, TradeState
from .finality import ExecutionFinality, execution_finality
from .formal import verify_runtime_transition_refinement, verify_three_process_coordination
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
    "domain.py",
    "exceptions.py",
    "state_machine.py",
    "ports.py",
    "guards.py",
    "event_queue.py",
    "journal.py",
    "mutex.py",
    "recovery.py",
    "session.py",
    "finality.py",
    "coordination.py",
    "coordinated_session.py",
    "formal.py",
    "verifier.py",
    "miniqmt/status.py",
    "miniqmt/adapter.py",
    "miniqmt/callbacks.py",
    "miniqmt/binding.py",
    "miniqmt/runtime_gate.py",
    "miniqmt/session_id.py",
    "miniqmt/runtime.py",
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


def _assert_v04_finality_refinement(snapshot: MachineSnapshot) -> None:
    """Check frozen v0.4 finality semantics over every reachable snapshot."""

    finality = execution_finality(snapshot)
    if snapshot.state in {TradeState.UNKNOWN, TradeState.CANCEL_REJECTED}:
        if finality is not ExecutionFinality.OPEN:
            raise RuntimeError(
                f"v0.4 finality violation: {snapshot.state.value} must remain OPEN"
            )
    if snapshot.state is TradeState.FAILED and snapshot.facts.unresolved_order:
        if finality is not ExecutionFinality.QUARANTINED:
            raise RuntimeError(
                "v0.4 finality violation: unresolved FAILED must be QUARANTINED"
            )
    if snapshot.state in {TradeState.FILLED, TradeState.CANCELLED, TradeState.REJECTED}:
        if finality is not ExecutionFinality.RESOLVED:
            raise RuntimeError(
                f"v0.4 finality violation: {snapshot.state.value} must be RESOLVED"
            )


def verify_state_machine(*, source_root: Path | None = None) -> dict[str, object]:
    """Fast single-process proof used by ExecutionSession.open()."""

    initial = initial_snapshot()
    queue = deque([initial])
    reachable = {initial}
    edges: set[tuple[MachineSnapshot, TradeEvent, MachineSnapshot]] = set()
    reached_declared_edges: set[tuple[TradeState, TradeEvent]] = set()

    while queue:
        current = queue.popleft()
        assert_invariants(current)
        _assert_v04_finality_refinement(current)
        for event in TRANSITIONS[current.state]:
            try:
                successor = _step(current, event)
            except InvalidTransition:
                continue
            assert_invariants(successor)
            _assert_v04_finality_refinement(successor)
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
        raise RuntimeError(
            f"unreachable declared transitions: "
            f"{sorted((s.value, e.value) for s, e in missing_edges)}"
        )
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
        "v0_4_finality_invariant_violations": 0,
        "transition_spec_sha256": transition_spec_sha256(),
        "execution_source_sha256": execution_source_sha256(source_root),
    }


def verify_release_model(*, source_root: Path | None = None) -> dict[str, object]:
    """Full release gate: abstract machine + Python refinement + 3-process product.

    The expensive three-process state-space proof intentionally does not run on
    every ExecutionSession.open(); the CLI/CI release gate invokes this function.
    """

    report = verify_state_machine(source_root=source_root)
    report["implementation_refinement"] = verify_runtime_transition_refinement(
        source_root=source_root
    )
    report["three_process_coordination"] = verify_three_process_coordination()
    report["release_formal_verification"] = "PASS"
    return report


def transition_spec_sha256() -> str:
    rows: list[str] = []
    for state in sorted(TRANSITIONS, key=lambda s: s.value):
        for event, target in sorted(
            TRANSITIONS[state].items(), key=lambda item: item[0].value
        ):
            rows.append(f"{state.value}|{event.value}|{target.value}\n")
    return hashlib.sha256("".join(rows).encode("utf-8")).hexdigest()


def _resolve_package_root(source_root: Path | None) -> Path:
    if source_root is None:
        return Path(__file__).resolve().parent
    root = Path(source_root).resolve()
    candidates = (
        root / "src" / "qmt_execution_core",
        root / "qmt_execution_core",
        root,
    )
    for candidate in candidates:
        if (candidate / "verifier.py").is_file() and (candidate / "domain.py").is_file():
            return candidate
    raise FileNotFoundError(f"qmt_execution_core package root not found under {root}")


def execution_source_sha256(source_root: Path | None = None) -> str:
    root = _resolve_package_root(source_root)
    digest = hashlib.sha256()
    for relative in PROTECTED_EXECUTION_SOURCES:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"protected execution source missing: {relative}")
        canonical_name = f"qmt_execution_core/{relative}"
        digest.update(canonical_name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(
            path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        )
        digest.update(b"\0")
    return digest.hexdigest()
