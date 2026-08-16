from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .domain import (
    BrokerOrderStatus,
    PrecheckEvidence,
    SessionEvidence,
    Side,
    TradeEvent,
    TradeState,
)
from .exceptions import RecoveryAmbiguous
from .finality import ExecutionFinality, execution_finality
from .recovery import event_for_observation
from .state_machine import (
    InvalidTransition,
    InvariantViolation,
    MachineSnapshot,
    TERMINAL_STATES,
    TRANSITIONS,
    advance,
    assert_invariants,
    initial_snapshot,
)


@dataclass(frozen=True)
class RuntimeTransitionCall:
    source: str
    function: str
    line: int
    event: str | None


@dataclass(frozen=True)
class RuntimeStateWrite:
    source: str
    function: str
    line: int
    producer: str


@dataclass(frozen=True)
class FormalProcessSpec:
    account: int
    symbol: int
    side: Side
    required_cash: int


@dataclass(frozen=True)
class FormalScenario:
    name: str
    processes: tuple[FormalProcessSpec, FormalProcessSpec, FormalProcessSpec]
    cash_by_account: tuple[int, ...]
    require_all_working: bool = False
    require_cross_account_same_symbol_working: bool = False


_OBSERVATION_ENTRY_STATES = {
    TradeState.ACCEPTED,
    TradeState.WORKING,
    TradeState.PARTIALLY_FILLED,
    TradeState.PENDING_CANCEL,
    TradeState.CANCELLING,
    TradeState.CANCEL_REJECTED,
    TradeState.UNKNOWN,
}

_RUNTIME_SOURCE_FILES = ("session.py", "coordinated_session.py")
_EVENT_SOURCE_FILES = ("session.py", "recovery.py")


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


def _resolve_package_root(source_root: Path | None = None) -> Path:
    if source_root is None:
        return Path(__file__).resolve().parent
    root = Path(source_root).resolve()
    candidates = (
        root / "src" / "qmt_execution_core",
        root / "qmt_execution_core",
        root,
    )
    for candidate in candidates:
        if (candidate / "session.py").is_file() and (
            candidate / "state_machine.py"
        ).is_file():
            return candidate
    raise FileNotFoundError(f"qmt_execution_core package root not found under {root}")


def _iter_function_nodes(tree: ast.AST) -> Iterable[tuple[str, ast.AST]]:
    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.nodes: list[tuple[str, ast.AST]] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.nodes.append((node.name, node))
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.nodes.append((node.name, node))
            self.generic_visit(node)

    visitor = Visitor()
    visitor.visit(tree)
    return tuple(visitor.nodes)


def _trade_event_name(node: ast.AST) -> str | None:
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "TradeEvent"
    ):
        return node.attr
    return None


def _target_contains_snapshot(target: ast.AST) -> bool:
    if (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
        and target.attr == "_snapshot"
    ):
        return True
    if isinstance(target, (ast.Tuple, ast.List)):
        return any(_target_contains_snapshot(item) for item in target.elts)
    return False


def _producer_name(node: ast.AST | None) -> str:
    if not isinstance(node, ast.Call):
        return type(node).__name__ if node is not None else "None"
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts: list[str] = [func.attr]
        value = func.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            parts.append(value.id)
        return ".".join(reversed(parts))
    return type(func).__name__


def _extract_source_surface(
    source_root: Path | None = None,
) -> tuple[tuple[RuntimeTransitionCall, ...], tuple[RuntimeStateWrite, ...], set[str]]:
    root = _resolve_package_root(source_root)
    calls: list[RuntimeTransitionCall] = []
    writes: list[RuntimeStateWrite] = []
    event_refs: set[str] = set()

    for relative in _EVENT_SOURCE_FILES:
        tree = ast.parse((root / relative).read_text(encoding="utf-8"), filename=relative)
        for function, fn_node in _iter_function_nodes(tree):
            for node in ast.walk(fn_node):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_transition"
                    and node.args
                ):
                    calls.append(
                        RuntimeTransitionCall(
                            source=relative,
                            function=function,
                            line=int(getattr(node, "lineno", 0)),
                            event=_trade_event_name(node.args[0]),
                        )
                    )
                if (
                    relative == "recovery.py"
                    and function == "event_for_observation"
                    and isinstance(node, ast.Attribute)
                ):
                    event_name = _trade_event_name(node)
                    if event_name is not None:
                        event_refs.add(event_name)

    for relative in _RUNTIME_SOURCE_FILES:
        tree = ast.parse((root / relative).read_text(encoding="utf-8"), filename=relative)
        for function, fn_node in _iter_function_nodes(tree):
            for node in ast.walk(fn_node):
                value: ast.AST | None = None
                targets: tuple[ast.AST, ...] = ()
                if isinstance(node, ast.Assign):
                    value = node.value
                    targets = tuple(node.targets)
                elif isinstance(node, ast.AnnAssign):
                    value = node.value
                    targets = (node.target,)
                elif isinstance(node, ast.AugAssign):
                    value = node.value
                    targets = (node.target,)
                if targets and any(_target_contains_snapshot(target) for target in targets):
                    writes.append(
                        RuntimeStateWrite(
                            source=relative,
                            function=function,
                            line=int(getattr(node, "lineno", 0)),
                            producer=_producer_name(value),
                        )
                    )

    return tuple(calls), tuple(writes), event_refs


def verify_runtime_transition_refinement(
    *, source_root: Path | None = None
) -> dict[str, object]:
    """Extract the runtime mutation surface and prove it refines TRANSITIONS.

    This is intentionally separate from the abstract reachability proof.  It
    checks the Python implementation that emits events and mutates `_snapshot`.
    """

    calls, writes, recovery_event_refs = _extract_source_surface(source_root)

    declared_events = {
        event.name for event_map in TRANSITIONS.values() for event in event_map
    }
    literal_events = {call.event for call in calls if call.event is not None}
    runtime_events = literal_events | recovery_event_refs

    unknown_events = runtime_events - declared_events
    if unknown_events:
        raise RuntimeError(
            f"runtime emits events not declared by TRANSITIONS: {sorted(unknown_events)}"
        )

    missing_runtime_emitters = declared_events - runtime_events
    if missing_runtime_emitters:
        raise RuntimeError(
            "declared transition events have no runtime emitter/refinement source: "
            f"{sorted(missing_runtime_emitters)}"
        )

    dynamic_calls = [call for call in calls if call.event is None]
    dynamic_signature = {(call.source, call.function) for call in dynamic_calls}
    if dynamic_signature != {("session.py", "_apply_observation")}:
        raise RuntimeError(
            "unexpected dynamic _transition call sites: "
            f"{sorted(dynamic_signature)}"
        )

    expected_writes = {
        ("session.py", "__init__", "MachineSnapshot"),
        ("session.py", "open", "self.journal.open"),
        ("session.py", "_transition", "advance"),
    }
    actual_writes = {
        (write.source, write.function, write.producer) for write in writes
    }
    if actual_writes != expected_writes:
        raise RuntimeError(
            "execution state mutation surface drifted: "
            f"expected={sorted(expected_writes)} actual={sorted(actual_writes)}"
        )

    illegal_observation_edges: list[tuple[str, str, str]] = []
    legal_observation_edges = 0
    ambiguous_observation_pairs = 0
    no_op_observation_pairs = 0
    for state in sorted(_OBSERVATION_ENTRY_STATES, key=lambda item: item.value):
        for status in BrokerOrderStatus:
            try:
                event = event_for_observation(state, status)
            except RecoveryAmbiguous:
                ambiguous_observation_pairs += 1
                continue
            if event is None:
                no_op_observation_pairs += 1
                continue
            legal_observation_edges += 1
            if event not in TRANSITIONS[state]:
                illegal_observation_edges.append(
                    (state.value, status.value, event.value)
                )

    if illegal_observation_edges:
        raise RuntimeError(
            "broker observation refinement emits illegal state-machine edges: "
            f"{illegal_observation_edges}"
        )

    transition_writes = [
        write
        for write in writes
        if write.source == "session.py" and write.function == "_transition"
    ]
    if len(transition_writes) != 1 or transition_writes[0].producer != "advance":
        raise RuntimeError("_transition no longer delegates exclusively to advance()")

    return {
        "method": (
            "AST runtime state-mutation extraction + exhaustive "
            "broker-observation refinement"
        ),
        "direct_transition_call_sites": len(calls),
        "literal_runtime_events": len(literal_events),
        "recovery_runtime_events": len(recovery_event_refs),
        "dynamic_transition_call_sites": len(dynamic_calls),
        "runtime_state_write_sites": len(writes),
        "observation_entry_states": len(_OBSERVATION_ENTRY_STATES),
        "broker_order_statuses": len(BrokerOrderStatus),
        "legal_observation_edges": legal_observation_edges,
        "ambiguous_observation_pairs": ambiguous_observation_pairs,
        "no_op_observation_pairs": no_op_observation_pairs,
        "illegal_observation_edges": 0,
        "undeclared_runtime_events": 0,
        "declared_events_without_runtime_emitter": 0,
        "hidden_runtime_state_mutations": 0,
    }


@dataclass(frozen=True)
class _LocalGraph:
    snapshots: tuple[MachineSnapshot, ...]
    ids: dict[MachineSnapshot, int]
    edges: dict[int, tuple[tuple[TradeEvent, int], ...]]
    by_event: dict[tuple[int, TradeEvent], int]


def _build_local_graph() -> _LocalGraph:
    initial = initial_snapshot()
    queue = deque([initial])
    reachable = {initial}
    raw_edges: dict[MachineSnapshot, list[tuple[TradeEvent, MachineSnapshot]]] = {}

    while queue:
        current = queue.popleft()
        assert_invariants(current)
        current_edges: list[tuple[TradeEvent, MachineSnapshot]] = []
        for event in TRANSITIONS[current.state]:
            try:
                successor = _step(current, event)
            except (InvalidTransition, InvariantViolation):
                continue
            assert_invariants(successor)
            current_edges.append((event, successor))
            if successor not in reachable:
                reachable.add(successor)
                queue.append(successor)
        raw_edges[current] = current_edges

    snapshots = tuple(
        sorted(reachable, key=lambda snap: (snap.state.value, repr(snap.facts)))
    )
    ids = {snapshot: index for index, snapshot in enumerate(snapshots)}
    edges: dict[int, tuple[tuple[TradeEvent, int], ...]] = {}
    by_event: dict[tuple[int, TradeEvent], int] = {}
    for snapshot in snapshots:
        sid = ids[snapshot]
        normalized: list[tuple[TradeEvent, int]] = []
        for event, successor in raw_edges.get(snapshot, ()):
            target = ids[successor]
            normalized.append((event, target))
            by_event[(sid, event)] = target
        edges[sid] = tuple(normalized)
    return _LocalGraph(
        snapshots=snapshots,
        ids=ids,
        edges=edges,
        by_event=by_event,
    )


def _encode_process(snapshot_id: int, held: bool) -> int:
    return snapshot_id * 2 + int(held)


def _decode_process(code: int) -> tuple[int, bool]:
    return code // 2, bool(code % 2)


def _replace_process(
    state: tuple[int, int, int], index: int, code: int
) -> tuple[int, int, int]:
    values = list(state)
    values[index] = code
    return values[0], values[1], values[2]


def _held(state: tuple[int, int, int], index: int) -> bool:
    return bool(state[index] % 2)


def _claim_free(
    state: tuple[int, int, int], scenario: FormalScenario, index: int
) -> bool:
    spec = scenario.processes[index]
    for other in range(3):
        if other == index or not _held(state, other):
            continue
        other_spec = scenario.processes[other]
        if other_spec.account == spec.account and other_spec.symbol == spec.symbol:
            return False
    return True


def _active_reserved_cash(
    state: tuple[int, int, int],
    scenario: FormalScenario,
    *,
    account: int,
    exclude: int | None = None,
) -> int:
    total = 0
    for index, spec in enumerate(scenario.processes):
        if index == exclude or not _held(state, index):
            continue
        if spec.account == account and spec.side is Side.BUY:
            total += spec.required_cash
    return total


def _can_prepare(
    state: tuple[int, int, int], scenario: FormalScenario, index: int
) -> bool:
    spec = scenario.processes[index]
    if not _claim_free(state, scenario, index):
        return False
    if spec.side is not Side.BUY:
        return True
    capacity = scenario.cash_by_account[spec.account]
    already_reserved = _active_reserved_cash(
        state, scenario, account=spec.account, exclude=index
    )
    return spec.required_cash <= capacity - already_reserved


def _can_restore(
    state: tuple[int, int, int], scenario: FormalScenario, index: int
) -> bool:
    # restore() does not authorize a new broker side effect, so it deliberately
    # does not re-check cash. It still preserves same-account symbol exclusivity.
    return _claim_free(state, scenario, index)


def _sync_phase(
    state: tuple[int, int, int],
    scenario: FormalScenario,
    graph: _LocalGraph,
    *,
    index: int,
    target_sid: int,
    current_held: bool,
    entered_submitted: bool = False,
) -> tuple[int, int, int] | None:
    snapshot = graph.snapshots[target_sid]
    finality = execution_finality(snapshot)
    if entered_submitted:
        return _replace_process(state, index, _encode_process(target_sid, False))
    if finality is ExecutionFinality.RESOLVED:
        return _replace_process(state, index, _encode_process(target_sid, False))
    if current_held:
        return _replace_process(state, index, _encode_process(target_sid, True))
    if not _can_restore(state, scenario, index):
        return None
    return _replace_process(state, index, _encode_process(target_sid, True))


def _global_successors(
    state: tuple[int, int, int], scenario: FormalScenario, graph: _LocalGraph
) -> tuple[tuple[str, tuple[int, int, int]], ...]:
    successors: list[tuple[str, tuple[int, int, int]]] = []

    for index in range(3):
        sid, held = _decode_process(state[index])
        snapshot = graph.snapshots[sid]

        if snapshot.state is TradeState.SUBMITTED:
            if not held:
                if _can_prepare(state, scenario, index):
                    successors.append(
                        (
                            f"p{index}:coordination_ok",
                            _replace_process(state, index, _encode_process(sid, True)),
                        )
                    )
                else:
                    reject_sid = graph.by_event.get(
                        (sid, TradeEvent.PRE_BROKER_REJECTED)
                    )
                    if reject_sid is not None:
                        successors.append(
                            (
                                f"p{index}:coordination_rejected",
                                _replace_process(
                                    state, index, _encode_process(reject_sid, False)
                                ),
                            )
                        )

                abort_sid = graph.by_event.get((sid, TradeEvent.PRE_BROKER_ABORTED))
                if abort_sid is not None:
                    successors.append(
                        (
                            f"p{index}:coordination_abort",
                            _replace_process(
                                state, index, _encode_process(abort_sid, False)
                            ),
                        )
                    )

                # Crash after durable intent but before coordination. No broker
                # side effect occurred, but restart cannot infer that only from
                # the journal; it fails closed and coordinated open restores a
                # claim if ownership is still available.
                restart_sid = graph.by_event.get((sid, TradeEvent.RESTART_RECOVERY))
                if restart_sid is not None:
                    failed_sid = graph.by_event.get(
                        (restart_sid, TradeEvent.RECOVERY_FAILED)
                    )
                    if failed_sid is not None and _can_restore(
                        state, scenario, index
                    ):
                        successors.append(
                            (
                                f"p{index}:restart_precoord_quarantine",
                                _replace_process(
                                    state, index, _encode_process(failed_sid, True)
                                ),
                            )
                        )
                continue

            # Coordination is durable. A synchronous project-sidecar abort
            # releases resources before PRE_BROKER_ABORTED is persisted.
            abort_sid = graph.by_event.get((sid, TradeEvent.PRE_BROKER_ABORTED))
            if abort_sid is not None:
                successors.append(
                    (
                        f"p{index}:sidecar_abort",
                        _replace_process(
                            state, index, _encode_process(abort_sid, False)
                        ),
                    )
                )

            for event in (
                TradeEvent.SUBMIT_ACCEPTED,
                TradeEvent.SUBMIT_AMBIGUOUS,
                TradeEvent.SUBMIT_REJECTED,
                TradeEvent.RESTART_RECOVERY,
            ):
                target_sid = graph.by_event.get((sid, event))
                if target_sid is None:
                    continue
                release = event is TradeEvent.SUBMIT_REJECTED
                successors.append(
                    (
                        f"p{index}:{event.value}",
                        _replace_process(
                            state, index, _encode_process(target_sid, not release)
                        ),
                    )
                )
            continue

        for event, target_sid in graph.edges[sid]:
            # The three-process product verifies one logical execution lifecycle
            # per independent process. Cross-cycle idempotency remains covered by
            # the existing single-process verifier and journal tests.
            if event is TradeEvent.NEXT_CYCLE:
                continue
            entered_submitted = (
                event is TradeEvent.INTENT_PERSISTED
                and graph.snapshots[target_sid].state is TradeState.SUBMITTED
            )
            candidate = _sync_phase(
                state,
                scenario,
                graph,
                index=index,
                target_sid=target_sid,
                current_held=held,
                entered_submitted=entered_submitted,
            )
            if candidate is not None:
                successors.append((f"p{index}:{event.value}", candidate))

    return tuple(successors)


def _assert_global_invariants(
    state: tuple[int, int, int], scenario: FormalScenario, graph: _LocalGraph
) -> None:
    for left in range(3):
        lsid, lheld = _decode_process(state[left])
        lsnap = graph.snapshots[lsid]
        lspec = scenario.processes[left]
        lfinality = execution_finality(lsnap)

        if lheld:
            if lfinality is ExecutionFinality.RESOLVED:
                raise RuntimeError(
                    f"{scenario.name}: RESOLVED process p{left} retained shared resources"
                )
            if not lsnap.facts.intent_persisted:
                raise RuntimeError(
                    f"{scenario.name}: p{left} holds resources without durable intent"
                )
        elif lfinality in {ExecutionFinality.OPEN, ExecutionFinality.QUARANTINED}:
            if lsnap.state is not TradeState.SUBMITTED:
                raise RuntimeError(
                    f"{scenario.name}: unresolved p{left} lost shared claim"
                )

        if lsnap.state in {TradeState.UNKNOWN, TradeState.CANCEL_REJECTED} and not lheld:
            raise RuntimeError(
                f"{scenario.name}: recoverable p{left} does not hold symbol claim"
            )
        if (
            lsnap.state is TradeState.FAILED
            and lsnap.facts.unresolved_order
            and not lheld
        ):
            raise RuntimeError(
                f"{scenario.name}: quarantined FAILED p{left} released symbol claim"
            )

        for right in range(left + 1, 3):
            _, rheld = _decode_process(state[right])
            if not (lheld and rheld):
                continue
            rspec = scenario.processes[right]
            if lspec.account == rspec.account and lspec.symbol == rspec.symbol:
                raise RuntimeError(
                    f"{scenario.name}: duplicate unresolved claim on "
                    f"(account={lspec.account}, symbol={lspec.symbol})"
                )

    # Restored quarantine reservations may conservatively exceed the latest
    # broker cash snapshot, but they never authorize a broker side effect. The
    # safety property is therefore stated over broker-invoked active BUYs.
    for account, capacity in enumerate(scenario.cash_by_account):
        active_submitted = 0
        for index, spec in enumerate(scenario.processes):
            sid, held = _decode_process(state[index])
            if (
                held
                and spec.account == account
                and spec.side is Side.BUY
                and graph.snapshots[sid].facts.submitted_once
            ):
                active_submitted += spec.required_cash
        if active_submitted > capacity:
            raise RuntimeError(
                f"{scenario.name}: broker-invoked active BUY cash "
                f"{active_submitted} exceeds capacity {capacity}"
            )


def _is_all_working(state: tuple[int, int, int], graph: _LocalGraph) -> bool:
    return all(
        _held(state, index)
        and graph.snapshots[_decode_process(state[index])[0]].state
        is TradeState.WORKING
        for index in range(3)
    )


def _has_cross_account_same_symbol_working(
    state: tuple[int, int, int], scenario: FormalScenario, graph: _LocalGraph
) -> bool:
    for left in range(3):
        lsid, lheld = _decode_process(state[left])
        if not lheld or graph.snapshots[lsid].state is not TradeState.WORKING:
            continue
        for right in range(left + 1, 3):
            rsid, rheld = _decode_process(state[right])
            if not rheld or graph.snapshots[rsid].state is not TradeState.WORKING:
                continue
            lspec = scenario.processes[left]
            rspec = scenario.processes[right]
            if lspec.account != rspec.account and lspec.symbol == rspec.symbol:
                return True
    return False


def _verify_scenario(scenario: FormalScenario, graph: _LocalGraph) -> dict[str, object]:
    initial_sid = graph.ids[initial_snapshot()]
    initial = (
        _encode_process(initial_sid, False),
        _encode_process(initial_sid, False),
        _encode_process(initial_sid, False),
    )
    queue = deque([initial])
    reachable = {initial}
    edges = 0
    all_working_reached = False
    cross_account_same_symbol_working_reached = False
    legitimate_terminal_deadlocks = 0

    while queue:
        current = queue.popleft()
        _assert_global_invariants(current, scenario, graph)

        if _is_all_working(current, graph):
            all_working_reached = True
        if _has_cross_account_same_symbol_working(current, scenario, graph):
            cross_account_same_symbol_working_reached = True

        successors = _global_successors(current, scenario, graph)
        if not successors:
            snapshots = [
                graph.snapshots[_decode_process(code)[0]] for code in current
            ]
            if all(snapshot.state in TERMINAL_STATES for snapshot in snapshots):
                legitimate_terminal_deadlocks += 1
            else:
                raise RuntimeError(
                    f"{scenario.name}: unexpected global deadlock at "
                    f"{[(snapshot.state.value, execution_finality(snapshot).value) for snapshot in snapshots]}"
                )

        for _, successor in successors:
            _assert_global_invariants(successor, scenario, graph)
            edges += 1
            if successor not in reachable:
                reachable.add(successor)
                queue.append(successor)

    if scenario.require_all_working and not all_working_reached:
        raise RuntimeError(
            f"{scenario.name}: three processes never reached WORKING concurrently"
        )
    if (
        scenario.require_cross_account_same_symbol_working
        and not cross_account_same_symbol_working_reached
    ):
        raise RuntimeError(
            f"{scenario.name}: cross-account same-symbol concurrency witness missing"
        )

    return {
        "reachable_global_states": len(reachable),
        "reachable_interleaving_edges": edges,
        "all_three_working_reached": all_working_reached,
        "cross_account_same_symbol_working_reached": (
            cross_account_same_symbol_working_reached
        ),
        "legitimate_terminal_deadlocks": legitimate_terminal_deadlocks,
        "invariant_violations": 0,
    }


def verify_three_process_coordination() -> dict[str, object]:
    """Exhaustively verify four 3-process shared-account deployment classes."""

    graph = _build_local_graph()
    scenarios = (
        FormalScenario(
            name="same_account_three_distinct_symbols",
            processes=(
                FormalProcessSpec(0, 0, Side.BUY, 30),
                FormalProcessSpec(0, 1, Side.BUY, 30),
                FormalProcessSpec(0, 2, Side.BUY, 30),
            ),
            cash_by_account=(100,),
            require_all_working=True,
        ),
        FormalScenario(
            name="same_account_same_symbol_exclusion",
            processes=(
                FormalProcessSpec(0, 0, Side.BUY, 30),
                FormalProcessSpec(0, 0, Side.BUY, 30),
                FormalProcessSpec(0, 0, Side.SELL, 0),
            ),
            cash_by_account=(100,),
        ),
        FormalScenario(
            name="same_account_cash_contention",
            processes=(
                FormalProcessSpec(0, 0, Side.BUY, 60),
                FormalProcessSpec(0, 1, Side.BUY, 50),
                FormalProcessSpec(0, 2, Side.BUY, 40),
            ),
            cash_by_account=(100,),
        ),
        FormalScenario(
            name="cross_account_same_symbol_independence",
            processes=(
                FormalProcessSpec(0, 0, Side.BUY, 30),
                FormalProcessSpec(1, 0, Side.BUY, 30),
                FormalProcessSpec(0, 1, Side.BUY, 30),
            ),
            cash_by_account=(100, 100),
            require_all_working=True,
            require_cross_account_same_symbol_working=True,
        ),
    )

    reports = {scenario.name: _verify_scenario(scenario, graph) for scenario in scenarios}
    return {
        "method": (
            "exhaustive explicit-state interleaving of three independent "
            "ExecutionSession state machines with shared symbol/cash coordination"
        ),
        "processes": 3,
        "logical_execution_cycles_per_process": 1,
        "local_reachable_snapshots": len(graph.snapshots),
        "scenario_count": len(reports),
        "scenarios": reports,
        "total_reachable_global_states": sum(
            int(report["reachable_global_states"]) for report in reports.values()
        ),
        "total_reachable_interleaving_edges": sum(
            int(report["reachable_interleaving_edges"]) for report in reports.values()
        ),
        "same_symbol_exclusivity_violations": 0,
        "shared_cash_authorization_violations": 0,
        "resource_release_violations": 0,
        "quarantine_claim_violations": 0,
        "cross_symbol_concurrency_witness": bool(
            reports["same_account_three_distinct_symbols"]["all_three_working_reached"]
        ),
        "cross_account_same_symbol_concurrency_witness": bool(
            reports["cross_account_same_symbol_independence"][
                "cross_account_same_symbol_working_reached"
            ]
        ),
    }
