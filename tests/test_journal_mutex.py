from pathlib import Path

import pytest

from qmt_execution_core.domain import ExecutionRequest, Side
from qmt_execution_core.journal import ExecutionJournal
from qmt_execution_core.mutex import ConcurrentExecutionError, ExecutionMutex


def test_journal_constructor_does_not_create_file(tmp_path: Path):
    path = tmp_path / "journal.json"
    ExecutionJournal(path, execution_id="x")
    assert not path.exists()


def test_journal_open_creates_atomically_and_persists_intent(tmp_path: Path):
    path = tmp_path / "journal.json"
    journal = ExecutionJournal(path, execution_id="x")
    snapshot, existed = journal.open()
    assert not existed
    assert path.exists()
    journal.persist_intent(ExecutionRequest("c1", "510300.SH", Side.BUY, 100, 4.7, "demo", "r1"))
    reopened = ExecutionJournal(path, execution_id="x")
    _, existed = reopened.open()
    assert existed
    assert reopened.data["intent"]["order_remark"] == "r1"


def test_mutex_excludes_second_owner(tmp_path: Path):
    path = tmp_path / "exec.lock"
    a = ExecutionMutex(path)
    b = ExecutionMutex(path)
    a.acquire()
    try:
        with pytest.raises(ConcurrentExecutionError):
            b.acquire()
    finally:
        a.release()
    b.acquire()
    b.release()
