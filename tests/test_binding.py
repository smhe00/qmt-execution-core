from pathlib import Path
from types import SimpleNamespace

import pytest

from qmt_execution_core.exceptions import AccountBindingError
from qmt_execution_core.miniqmt.binding import (
    QmtAccountBinding,
    load_account_binding,
    qmt_path_fingerprint,
    select_bound_account,
    verify_bound_account_healthy,
)


class Account:
    def __init__(self, account_id):
        self.account_id = account_id


class Trader:
    def __init__(self):
        self.infos = [SimpleNamespace(account_id="A123", account_type=2)]
        self.statuses = [
            SimpleNamespace(account_id="A123", account_type=2, status=0)
        ]

    def query_account_infos(self):
        return self.infos

    def query_account_status(self):
        return self.statuses


def test_binding_round_trip_has_no_plaintext_account(tmp_path):
    qmt = tmp_path / "userdata_mini"
    qmt.mkdir()
    path = tmp_path / "binding.json"
    binding = QmtAccountBinding.create(
        environment="simulation",
        account_type=2,
        account_id="A123",
        qmt_path=qmt,
    )
    binding.write(path)
    text = path.read_text()
    assert "A123" not in text
    loaded = load_account_binding(path, environment="simulation", qmt_path=qmt)
    assert loaded == binding


def test_binding_fails_on_qmt_path_mismatch(tmp_path):
    qmt = tmp_path / "qmt1"
    qmt.mkdir()
    other = tmp_path / "qmt2"
    other.mkdir()
    path = tmp_path / "binding.json"
    QmtAccountBinding.create(
        environment="simulation",
        account_type=2,
        account_id="A123",
        qmt_path=qmt,
    ).write(path)
    with pytest.raises(AccountBindingError):
        load_account_binding(path, environment="simulation", qmt_path=other)


def test_bound_account_requires_exact_type_and_status(tmp_path):
    qmt = tmp_path / "qmt"
    qmt.mkdir()
    binding = QmtAccountBinding.create(
        environment="simulation",
        account_type=2,
        account_id="A123",
        qmt_path=qmt,
    )
    trader = Trader()
    bound = select_bound_account(
        trader,
        binding=binding,
        security_account_type=2,
        account_status_ok=0,
        stock_account_factory=Account,
        delay_seconds=0,
    )
    assert bound.account_id == "A123"

    trader.statuses = [SimpleNamespace(account_id="A123", account_type=3, status=0)]
    with pytest.raises(AccountBindingError):
        verify_bound_account_healthy(
            trader,
            bound,
            security_account_type=2,
            account_status_ok=0,
            delay_seconds=0,
        )

    trader.statuses = [SimpleNamespace(account_id="A123", account_type=2, status=3)]
    with pytest.raises(AccountBindingError):
        verify_bound_account_healthy(
            trader,
            bound,
            security_account_type=2,
            account_status_ok=0,
            delay_seconds=0,
        )


def test_bound_account_duplicate_info_fails_closed(tmp_path):
    qmt = tmp_path / "qmt"
    qmt.mkdir()
    binding = QmtAccountBinding.create(
        environment="simulation",
        account_type=2,
        account_id="A123",
        qmt_path=qmt,
    )
    trader = Trader()
    trader.infos.append(SimpleNamespace(account_id="A123", account_type=2))
    with pytest.raises(AccountBindingError):
        select_bound_account(
            trader,
            binding=binding,
            security_account_type=2,
            account_status_ok=0,
            stock_account_factory=Account,
            delay_seconds=0,
        )
