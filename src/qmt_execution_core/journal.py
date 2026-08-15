from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .domain import ExecutionRequest, SafetyFacts, TradeEvent, TradeState
from .state_machine import MachineSnapshot, assert_invariants


JOURNAL_SCHEMA_VERSION = 1
MAX_HISTORY = 1000


class JournalError(RuntimeError):
    pass


class JournalIntegrityError(JournalError):
    pass


class ExecutionJournal:
    """Crash-safe JSON journal.

    Construction performs no I/O. `open()` must be called while the caller owns
    the execution mutex, so journal creation/load cannot race another executor.
    """

    def __init__(self, path: Path | str, *, execution_id: str) -> None:
        if type(execution_id) is not str or not execution_id:
            raise ValueError("execution_id must be a non-empty string")
        self.path = Path(path)
        self.execution_id = execution_id
        self.payload: dict[str, object] | None = None

    @property
    def is_open(self) -> bool:
        return self.payload is not None

    def open(self) -> tuple[MachineSnapshot, bool]:
        if self.payload is not None:
            raise JournalError("journal already open")
        if self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise JournalIntegrityError("journal is unreadable") from exc
            self._validate_payload(payload)
            self.payload = payload
            return self.machine_snapshot(), True

        now = datetime.now().astimezone().isoformat()
        snapshot = MachineSnapshot()
        self.payload = {
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "execution_id": self.execution_id,
            "created_at": now,
            "updated_at": now,
            "event_count": 0,
            "machine": self._snapshot_payload(snapshot),
            "history": [],
            "data": {},
        }
        self._write()
        return snapshot, False

    def machine_snapshot(self) -> MachineSnapshot:
        payload = self._require_open()
        machine = payload.get("machine")
        if not isinstance(machine, dict):
            raise JournalIntegrityError("journal machine payload missing")
        try:
            state = TradeState(machine["state"])
            facts_payload = machine["facts"]
            if not isinstance(facts_payload, dict):
                raise TypeError
            expected = set(SafetyFacts.__dataclass_fields__)
            if set(facts_payload) != expected:
                raise ValueError
            if any(type(facts_payload[k]) is not bool for k in expected):
                raise TypeError
            snapshot = MachineSnapshot(state=state, facts=SafetyFacts(**facts_payload))
        except (KeyError, TypeError, ValueError) as exc:
            raise JournalIntegrityError("invalid machine snapshot") from exc
        assert_invariants(snapshot)
        return snapshot

    def transition(self, event: TradeEvent, snapshot: MachineSnapshot, *, details: dict | None = None) -> None:
        payload = self._require_open()
        history = payload.get("history")
        if not isinstance(history, list):
            raise JournalIntegrityError("journal history is invalid")
        seq = int(payload.get("event_count", 0)) + 1
        at = datetime.now().astimezone().isoformat()
        history.append({
            "sequence": seq,
            "at": at,
            "event": event.value,
            "state": snapshot.state.value,
            "details": dict(details or {}),
        })
        payload["history"] = history[-MAX_HISTORY:]
        payload["event_count"] = seq
        payload["machine"] = self._snapshot_payload(snapshot)
        payload["updated_at"] = at
        self._write()

    def persist_intent(self, request: ExecutionRequest) -> None:
        data = self.data
        if "intent" in data:
            raise JournalIntegrityError("journal already contains an intent")
        data["intent"] = {
            "client_order_id": request.client_order_id,
            "symbol": request.symbol,
            "side": request.side.value,
            "qty": request.qty,
            "limit_price": float(request.limit_price),
            "strategy_id": request.strategy_id,
            "order_remark": request.order_remark,
        }
        data["reservation"] = {
            "qty": request.qty,
            "notional": request.qty * float(request.limit_price),
            "persisted": True,
        }
        self._write_data(data)

    def persist_broker_order_id(self, order_id: int) -> None:
        if type(order_id) is not int or order_id <= 0:
            raise ValueError("broker order id must be a positive plain int")
        data = self.data
        existing = data.get("broker_order_id")
        if existing is not None and existing != order_id:
            raise JournalIntegrityError("broker order id changed for one execution")
        data["broker_order_id"] = order_id
        self._write_data(data)

    def persist_cancel_intent(self) -> None:
        data = self.data
        data["cancel_intent"] = {
            "persisted": True,
            "at": datetime.now().astimezone().isoformat(),
        }
        self._write_data(data)

    def update_observation(self, **fields: object) -> None:
        data = self.data
        observation = dict(data.get("last_observation") or {})
        observation.update(fields)
        data["last_observation"] = observation
        self._write_data(data)

    @property
    def data(self) -> dict[str, object]:
        payload = self._require_open()
        data = payload.get("data")
        if not isinstance(data, dict):
            raise JournalIntegrityError("journal data is invalid")
        return dict(data)

    def bind_verification(self, *, transition_spec_sha256: str, execution_source_sha256: str) -> None:
        for name, value in (("transition_spec_sha256", transition_spec_sha256), ("execution_source_sha256", execution_source_sha256)):
            if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value.lower()):
                raise ValueError(f"{name} must be a SHA-256 hex digest")
        data = self.data
        data["formal_verification"] = {
            "transition_spec_sha256": transition_spec_sha256.lower(),
            "execution_source_sha256": execution_source_sha256.lower(),
        }
        self._write_data(data)

    def verification_matches(self, *, transition_spec_sha256: str, execution_source_sha256: str) -> bool:
        bound = self.data.get("formal_verification")
        return isinstance(bound, dict) and bound.get("transition_spec_sha256") == transition_spec_sha256 and bound.get("execution_source_sha256") == execution_source_sha256

    def clear_cycle_data(self) -> None:
        payload = self._require_open()
        current = self.data
        preserved = {}
        if "formal_verification" in current:
            preserved["formal_verification"] = current["formal_verification"]
        payload["data"] = preserved
        payload["updated_at"] = datetime.now().astimezone().isoformat()
        self._write()

    def _write_data(self, data: dict[str, object]) -> None:
        payload = self._require_open()
        payload["data"] = data
        payload["updated_at"] = datetime.now().astimezone().isoformat()
        self._write()

    def _require_open(self) -> dict[str, object]:
        if self.payload is None:
            raise JournalError("journal is not open")
        return self.payload

    def _validate_payload(self, payload: object) -> None:
        if not isinstance(payload, dict):
            raise JournalIntegrityError("journal root must be an object")
        if payload.get("schema_version") != JOURNAL_SCHEMA_VERSION:
            raise JournalIntegrityError("journal schema mismatch")
        if payload.get("execution_id") != self.execution_id:
            raise JournalIntegrityError("journal execution_id mismatch")
        if not isinstance(payload.get("history"), list):
            raise JournalIntegrityError("journal history invalid")
        if not isinstance(payload.get("data"), dict):
            raise JournalIntegrityError("journal data invalid")

    @staticmethod
    def _snapshot_payload(snapshot: MachineSnapshot) -> dict[str, object]:
        return {"state": snapshot.state.value, "facts": asdict(snapshot.facts)}

    def _write(self) -> None:
        payload = self._require_open()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            for attempt in range(20):
                try:
                    os.replace(temporary, self.path)
                    return
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.05)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass
