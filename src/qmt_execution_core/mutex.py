from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class ConcurrentExecutionError(RuntimeError):
    pass


class ExecutionMutex:
    """Cross-process advisory lock for one execution session."""

    def __init__(self, path: Path | str, *, timeout_seconds: float = 0.0, poll_seconds: float = 0.2) -> None:
        if type(timeout_seconds) not in (int, float) or isinstance(timeout_seconds, bool):
            raise TypeError("timeout_seconds must be numeric")
        if type(poll_seconds) not in (int, float) or isinstance(poll_seconds, bool):
            raise TypeError("poll_seconds must be numeric")
        self.path = Path(path).resolve()
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self.poll_seconds = max(0.01, float(poll_seconds))
        self._handle: Any = None

    @property
    def owned(self) -> bool:
        return self._handle is not None

    def acquire(self) -> None:
        if self._handle is not None:
            raise RuntimeError("execution mutex already owned")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        deadline = time.monotonic() + self.timeout_seconds
        try:
            while True:
                try:
                    self._lock(handle)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise ConcurrentExecutionError("another executor owns the lock") from exc
                    time.sleep(min(self.poll_seconds, max(0.0, deadline - time.monotonic())))
            handle.seek(0)
            handle.truncate()
            handle.write(f"pid={os.getpid()} at={datetime.now().astimezone().isoformat()}\n".encode())
            handle.flush()
            os.fsync(handle.fileno())
            self._handle = handle
        except Exception:
            handle.close()
            raise

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    @staticmethod
    def _lock(handle: Any) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            if handle.read(1) == b"":
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
