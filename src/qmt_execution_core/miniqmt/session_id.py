from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..exceptions import SessionIdUnavailable
from ..mutex import ConcurrentExecutionError, ExecutionMutex
from .binding import qmt_path_fingerprint


@dataclass
class SessionIdLease:
    session_id: int
    mutex: ExecutionMutex
    _released: bool = False

    def release(self) -> None:
        if self._released:
            return
        self.mutex.release()
        self._released = True

    def __enter__(self) -> "SessionIdLease":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


class BoundedSessionIdAllocator:
    """Finite OS-lock-backed MiniQMT session-id allocator for shared mode."""

    def __init__(
        self,
        qmt_path: Path | str,
        *,
        pool_start: int = 100_000_000,
        pool_size: int = 1_000,
        attempts: int = 32,
    ) -> None:
        if type(pool_start) is not int or pool_start <= 0:
            raise ValueError("pool_start must be a positive plain int")
        if type(pool_size) is not int or pool_size <= 0:
            raise ValueError("pool_size must be a positive plain int")
        if type(attempts) is not int or attempts <= 0:
            raise ValueError("attempts must be a positive plain int")
        if pool_start + pool_size - 1 > 2_147_483_647:
            raise ValueError("session id pool exceeds signed 32-bit range")
        self.qmt_path = Path(qmt_path).expanduser().resolve(strict=False)
        self.pool_start = pool_start
        self.pool_size = pool_size
        self.attempts = min(attempts, pool_size)
        self._fingerprint = qmt_path_fingerprint(self.qmt_path)

    def lease_path(self, session_id: int) -> Path:
        if type(session_id) is not int or session_id <= 0:
            raise ValueError("session_id must be a positive plain int")
        return (
            Path(tempfile.gettempdir())
            / "qmt-execution-core"
            / "session-leases"
            / f"{self._fingerprint}-{session_id}.lock"
        )

    def candidate_ids(self, preferred_key: str) -> tuple[int, ...]:
        if type(preferred_key) is not str or not preferred_key:
            raise ValueError("preferred_key must be a non-empty string")
        seed = hashlib.sha256(preferred_key.encode("utf-8")).digest()
        offset = int.from_bytes(seed[:8], "big") % self.pool_size
        return tuple(
            self.pool_start + ((offset + index) % self.pool_size)
            for index in range(self.attempts)
        )

    def acquire_exact(self, session_id: int) -> SessionIdLease:
        mutex = ExecutionMutex(self.lease_path(session_id))
        try:
            mutex.acquire()
        except ConcurrentExecutionError as exc:
            raise SessionIdUnavailable(
                f"MiniQMT session id {session_id} is already leased"
            ) from exc
        return SessionIdLease(session_id=session_id, mutex=mutex)

    def acquire(self, preferred_key: str) -> SessionIdLease:
        last_error: Exception | None = None
        for session_id in self.candidate_ids(preferred_key):
            try:
                return self.acquire_exact(session_id)
            except SessionIdUnavailable as exc:
                last_error = exc
        raise SessionIdUnavailable(
            "no MiniQMT session id is available in the configured bounded attempts"
        ) from last_error
