import shutil
from pathlib import Path

import pytest

from qmt_execution_core.domain import PrecheckEvidence, SessionEvidence, TradeEvent, TradeState
from qmt_execution_core.state_machine import InvalidTransition, advance, initial_snapshot
from qmt_execution_core.verifier import execution_source_sha256, verify_state_machine


def session_evidence():
    return SessionEvidence(True, True, True)


def precheck_evidence():
    return PrecheckEvidence(True, True, True, True, True, True, True)


def to_submitted():
    s = initial_snapshot()
    s = advance(s, TradeEvent.SESSION_READY, session_evidence=session_evidence())
    s = advance(s, TradeEvent.TRIGGERED)
    s = advance(s, TradeEvent.BEGIN_PRECHECK)
    s = advance(s, TradeEvent.PRECHECK_VERIFIED, precheck_evidence=precheck_evidence())
    s = advance(s, TradeEvent.INTENT_PERSISTED)
    return s


def test_verified_precheck_is_required():
    s = initial_snapshot()
    s = advance(s, TradeEvent.SESSION_READY, session_evidence=session_evidence())
    s = advance(s, TradeEvent.TRIGGERED)
    s = advance(s, TradeEvent.BEGIN_PRECHECK)
    with pytest.raises(Exception):
        advance(s, TradeEvent.PRECHECK_VERIFIED)


def test_unknown_has_no_blind_retry():
    s = to_submitted()
    s = advance(s, TradeEvent.SUBMIT_AMBIGUOUS)
    assert s.state is TradeState.UNKNOWN
    with pytest.raises(InvalidTransition):
        advance(s, TradeEvent.INTENT_PERSISTED)
    with pytest.raises(InvalidTransition):
        advance(s, TradeEvent.SUBMIT_ACCEPTED)


def test_cancel_path_requires_durable_cancel_intent():
    s = to_submitted()
    s = advance(s, TradeEvent.SUBMIT_ACCEPTED)
    s = advance(s, TradeEvent.ORDER_WORKING)
    s = advance(s, TradeEvent.CANCEL_REQUESTED)
    assert s.facts.cancel_intent_persisted
    s = advance(s, TradeEvent.CANCEL_SENT)
    assert s.state is TradeState.CANCELLING
    s = advance(s, TradeEvent.CANCEL_CONFIRMED)
    assert s.state is TradeState.CANCELLED
    assert s.facts.terminal_order_confirmed
    assert not s.facts.unresolved_order


def test_formal_verifier_reaches_fixed_point():
    result = verify_state_machine()
    assert result["unreachable_states"] == 0
    assert result["unreachable_transitions"] == 0
    assert result["states_without_terminal_path"] == 0
    assert result["invariant_violations"] == 0


def test_source_hash_is_layout_independent(tmp_path):
    package_src = Path(__file__).resolve().parents[1] / "src" / "qmt_execution_core"
    installed_like = tmp_path / "site-packages" / "qmt_execution_core"
    shutil.copytree(package_src, installed_like)
    assert execution_source_sha256(package_src) == execution_source_sha256(installed_like)


def test_source_hash_fails_closed_when_protected_file_missing(tmp_path):
    package_src = Path(__file__).resolve().parents[1] / "src" / "qmt_execution_core"
    installed_like = tmp_path / "qmt_execution_core"
    shutil.copytree(package_src, installed_like)
    (installed_like / "miniqmt" / "runtime.py").unlink()
    with pytest.raises(FileNotFoundError):
        execution_source_sha256(installed_like)
