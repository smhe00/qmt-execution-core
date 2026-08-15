from __future__ import annotations

import queue
import threading
from enum import Enum
from typing import Callable

from .exceptions import EventQueueUnhealthy


class EventQueueState(str, Enum):
    NEW = "new"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


_STOP = object()


class SerialEventQueue:
    """Bounded single-consumer queue for broker callback isolation.

    Callback threads should call :meth:`try_emit`, which never blocks. If the
    queue is full or unhealthy, the queue enters FAILED and all new-order
    execution must be blocked by the caller's health probe.
    """

    def __init__(self, handler: Callable[[object], None], *, maxsize: int = 1024) -> None:
        if not callable(handler):
            raise TypeError("handler must be callable")
        if type(maxsize) is not int or maxsize <= 0:
            raise ValueError("maxsize must be a positive plain int")
        self._handler = handler
        self._queue: queue.Queue[object] = queue.Queue(maxsize=maxsize)
        self._state = EventQueueState.NEW
        self._failure: BaseException | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> EventQueueState:
        with self._lock:
            return self._state

    @property
    def failure(self) -> BaseException | None:
        with self._lock:
            return self._failure

    @property
    def healthy(self) -> bool:
        return self.state is EventQueueState.RUNNING

    def start(self) -> None:
        with self._lock:
            if self._state is EventQueueState.RUNNING:
                return
            if self._state is not EventQueueState.NEW:
                raise EventQueueUnhealthy(f"event queue cannot start from {self._state.value}")
            self._state = EventQueueState.RUNNING
            self._thread = threading.Thread(
                target=self._run,
                name="qmt-execution-event-queue",
                daemon=True,
            )
            self._thread.start()

    def emit(self, event: object) -> None:
        if not self.try_emit(event):
            raise EventQueueUnhealthy("event queue is not accepting events")

    def try_emit(self, event: object) -> bool:
        with self._lock:
            if self._state is not EventQueueState.RUNNING:
                return False
        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            self._fail(EventQueueUnhealthy("event queue overflow"))
            return False

    def stop(self) -> None:
        with self._lock:
            if self._state is EventQueueState.NEW:
                self._state = EventQueueState.STOPPED
                return
            if self._state in {EventQueueState.STOPPED, EventQueueState.FAILED}:
                return
            self._state = EventQueueState.STOPPED
        try:
            self._queue.put_nowait(_STOP)
        except queue.Full:
            # A stopped queue is already unhealthy for order execution; the
            # daemon thread will exit after pending items are drained.
            pass

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                self._handler(item)
            except BaseException as exc:  # safety infrastructure boundary
                self._fail(exc)
                return
            finally:
                self._queue.task_done()

            with self._lock:
                if self._state is EventQueueState.STOPPED and self._queue.empty():
                    return

    def _fail(self, exc: BaseException) -> None:
        with self._lock:
            self._failure = exc
            self._state = EventQueueState.FAILED
