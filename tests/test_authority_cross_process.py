"""Core 0.4.1 Account Runtime Authority — cross-process acceptance matrix.

Spec scenarios 10 (concurrent first bootstrap converges on one domain) and
12 (authority lock contention on Windows/POSIX).  Uses real OS processes so
the OS-backed authority lock is genuinely cross-process.
"""

from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

import pytest

from qmt_execution_core import AccountRuntimeAuthority
from qmt_execution_core.coordination import account_key_from_binding_identity
from qmt_execution_core.mutex import ConcurrentExecutionError, ExecutionMutex

_SHA = "a" * 64


def _account_key() -> str:
    return account_key_from_binding_identity(
        environment="simulation", account_type=2, account_id_sha256=_SHA,
    )


def _bootstrap_worker(root: str, account_key: str, queue) -> None:
    """Resolve (with atomic bootstrap) and report the resulting authority."""
    try:
        authority = AccountRuntimeAuthority(root).resolve(
            account_key=account_key,
            environment="simulation",
            account_type=2,
            account_id_sha256=_SHA,
            coordination_db_path=None,
            bootstrap=True,
        )
        queue.put(
            {
                "ok": True,
                "authority_id": authority.authority_id,
                "db_uuid": authority.coordination_db_uuid,
                "db_path": authority.coordination_db_path,
            }
        )
    except Exception as exc:  # noqa: BLE001 - report across process boundary
        queue.put({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def _lock_holder_worker(lock_path: str, hold_seconds: float, queue) -> None:
    mutex = ExecutionMutex(lock_path, timeout_seconds=5.0, poll_seconds=0.05)
    mutex.acquire()
    queue.put("held")
    time.sleep(hold_seconds)
    mutex.release()
    queue.put("released")


class TestCrossProcessBootstrap:
    def test_scenario10_concurrent_bootstrap_converges_on_one_domain(self, tmp_path):
        root = str(tmp_path / "auth")
        key = _account_key()
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        processes = [
            context.Process(target=_bootstrap_worker, args=(root, key, queue))
            for _ in range(2)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=30)
            assert process.exitcode == 0, process.exitcode
        results = [queue.get(timeout=5) for _ in range(2)]
        assert all(result["ok"] for result in results), results
        # Both processes converged on ONE authority + ONE certified DB.
        assert results[0]["authority_id"] == results[1]["authority_id"]
        assert results[0]["db_uuid"] == results[1]["db_uuid"]
        assert results[0]["db_path"] == results[1]["db_path"]
        authority_files = list(Path(root).glob("*.authority.json"))
        db_files = list(Path(root).glob("*.coordination.db"))
        assert len(authority_files) == 1
        assert len(db_files) == 1

    def test_scenario12_lock_contention_blocks_then_releases(self, tmp_path):
        lock_path = str(tmp_path / "auth" / f"{_account_key()}.authority.lock")
        Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        holder = context.Process(
            target=_lock_holder_worker, args=(lock_path, 1.5, queue)
        )
        holder.start()
        assert queue.get(timeout=5) == "held"
        # A second owner with a short deadline fails closed while held.
        with pytest.raises(ConcurrentExecutionError):
            ExecutionMutex(lock_path, timeout_seconds=0.5, poll_seconds=0.05).acquire()
        # After the holder releases, the same lock is acquirable.
        assert queue.get(timeout=5) == "released"
        mutex = ExecutionMutex(lock_path, timeout_seconds=2.0, poll_seconds=0.05)
        mutex.acquire()
        mutex.release()
        holder.join(timeout=10)
        assert holder.exitcode == 0
