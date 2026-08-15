import threading
import time

from qmt_execution_core.event_queue import EventQueueState, SerialEventQueue


def wait_until(predicate, timeout=1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


def test_event_queue_serializes_events():
    seen = []
    threads = []

    def handler(event):
        seen.append(event)
        threads.append(threading.get_ident())

    q = SerialEventQueue(handler, maxsize=8)
    q.start()
    assert q.try_emit(1)
    assert q.try_emit(2)
    wait_until(lambda: seen == [1, 2])
    assert len(set(threads)) == 1
    q.stop()
    q.join(1.0)


def test_event_queue_handler_failure_becomes_unhealthy():
    def handler(event):
        raise RuntimeError("boom")

    q = SerialEventQueue(handler, maxsize=4)
    q.start()
    assert q.try_emit("x")
    wait_until(lambda: q.state is EventQueueState.FAILED)
    assert not q.healthy


def test_stopped_queue_rejects_callback_events():
    q = SerialEventQueue(lambda e: None)
    q.start()
    q.stop()
    q.join(1.0)
    assert not q.try_emit("late")
    assert not q.healthy
