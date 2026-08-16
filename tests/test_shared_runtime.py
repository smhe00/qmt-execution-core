from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from qmt_execution_core import (
    ConservativeCashRequirementEstimator,
    ExecutionRequest,
    PrecheckEvidence,
    SQLiteExecutionCoordinator,
    SessionEvidence,
    Side,
    TradeState,
    account_key_from_binding_identity,
)
from qmt_execution_core.exceptions import RuntimeConfigurationError, SessionIdUnavailable
from qmt_execution_core.miniqmt import (
    MiniQmtRuntime,
    MiniQmtRuntimeConfig,
    QmtAccountBinding,
    account_id_fingerprint,
)


class XtConstant:
    SECURITY_ACCOUNT = 2
    ACCOUNT_STATUS_OK = 0
    FIX_PRICE = 11
    STOCK_BUY = 23
    STOCK_SELL = 24


class CallbackBase:
    pass


class StockAccount:
    def __init__(self, account_id: str):
        self.account_id = account_id


class AllowGuard:
    def verify_session(self):
        return SessionEvidence(True, True, True)

    def verify(self, request):
        return PrecheckEvidence(True, True, True, True, True, True, True)


class RawOrder:
    def __init__(
        self,
        *,
        order_id: int,
        symbol: str,
        order_type: int,
        qty: int,
        remark: str,
        strategy_name: str,
    ) -> None:
        self.order_id = order_id
        self.stock_code = symbol
        self.order_type = order_type
        self.order_volume = qty
        self.traded_volume = 0
        self.order_status = 50  # WORKING
        self.order_remark = remark
        self.strategy_name = strategy_name
        self.order_sysid = f"sys-{order_id}"
        self.status_msg = ""
        self.traded_price = 0.0


class FakeTrader:
    def __init__(
        self,
        path: str,
        session_id: int,
        *,
        account_id: str = "A123",
        cash: float = 100_000.0,
        connect_result: int = 0,
    ) -> None:
        self.path = path
        self.session_id = session_id
        self.account_id = account_id
        self.cash = cash
        self.connect_result = connect_result
        self.callback = None
        self.place_calls = 0
        self.order = None
        self.stopped = False

    def register_callback(self, callback):
        self.callback = callback

    def start(self):
        return None

    def stop(self):
        self.stopped = True

    def connect(self):
        return self.connect_result

    def subscribe(self, account):
        return 0

    def unsubscribe(self, account):
        return 0

    def query_account_infos(self):
        return [SimpleNamespace(account_id=self.account_id, account_type=2)]

    def query_account_status(self):
        return [SimpleNamespace(account_id=self.account_id, account_type=2, status=0)]

    def query_stock_asset(self, account):
        return SimpleNamespace(
            cash=self.cash,
            frozen_cash=0.0,
            market_value=0.0,
            total_asset=self.cash,
        )

    def order_stock(
        self,
        account,
        symbol,
        order_type,
        qty,
        price_type,
        price,
        strategy,
        remark,
    ):
        self.place_calls += 1
        self.order = RawOrder(
            order_id=1000 + self.place_calls,
            symbol=symbol,
            order_type=order_type,
            qty=qty,
            remark=remark,
            strategy_name=strategy,
        )
        return self.order.order_id

    def cancel_order_stock(self, account, order_id):
        return 0

    def query_stock_order(self, account, order_id):
        return self.order

    def query_stock_orders(self, account, cancelable_only=False):
        return [] if self.order is None else [self.order]


def _binding(
    tmp_path: Path,
    qmt_path: Path,
    *,
    account_id: str = "A123",
    name: str = "binding.json",
) -> Path:
    path = tmp_path / name
    QmtAccountBinding.create(
        environment="simulation",
        account_type=2,
        account_id=account_id,
        qmt_path=qmt_path,
    ).write(path)
    return path


def _config(
    tmp_path: Path,
    qmt_path: Path,
    binding_path: Path,
    *,
    strategy: str,
    session_id: int | None = None,
) -> MiniQmtRuntimeConfig:
    return MiniQmtRuntimeConfig(
        environment="simulation",
        qmt_path=qmt_path,
        binding_path=binding_path,
        journal_path=tmp_path / f"{strategy}-journal.json",
        lock_path=tmp_path / f"{strategy}-exec.lock",
        strategy_name=strategy,
        runtime_lock_mode="shared",
        session_id=session_id,
        session_id_pool_start=210_000_000,
        session_id_pool_size=16,
        session_id_attempts=4,
        query_delay_seconds=0,
    )


def _request(strategy: str, client: str, symbol: str) -> ExecutionRequest:
    return ExecutionRequest(
        client,
        symbol,
        Side.BUY,
        100,
        10.0,
        strategy,
        f"{strategy}-{client}",
    )


def _connect(config: MiniQmtRuntimeConfig, factory, *, estimator=True,
             coordinator=None, authority=None):
    return MiniQmtRuntime.connect(
        config,
        guard=AllowGuard(),
        trader_factory=factory,
        stock_account_factory=StockAccount,
        xtconstant=XtConstant,
        callback_base=CallbackBase,
        cash_estimator=(
            ConservativeCashRequirementEstimator(safety_buffer=10.0)
            if estimator
            else None
        ),
        coordinator=coordinator,
        authority=authority,
    )


def test_shared_mode_without_explicit_path_uses_authority(tmp_path: Path):
    # Core 0.4.1: production shared mode resolves the Account Runtime
    # Authority (canonical per-account domain) — the strategy never selects
    # the coordination DB path or the authority root.  The Authority must be
    # initialized by the explicit operator bootstrap first; the runtime only
    # verifies.
    from qmt_execution_core import AccountRuntimeAuthority

    qmt_path = tmp_path / "userdata_mini"
    qmt_path.mkdir()
    binding = _binding(tmp_path, qmt_path)
    config = _config(tmp_path, qmt_path, binding, strategy="a")
    store = AccountRuntimeAuthority(tmp_path / "authority")
    binding_payload = json.loads(binding.read_text(encoding="utf-8"))
    account_key = account_key_from_binding_identity(
        environment="simulation", account_type=2,
        account_id_sha256=binding_payload["account_id_sha256"],
    )
    store.resolve(
        account_key=account_key, environment="simulation", account_type=2,
        account_id_sha256=binding_payload["account_id_sha256"],
        coordination_db_path=None, bootstrap=True,
    )
    runtime = _connect(
        config, lambda path, sid: FakeTrader(path, sid), authority=store,
    )
    try:
        from qmt_execution_core.coordinated_session import CoordinatedExecutionSession

        assert isinstance(runtime.session, CoordinatedExecutionSession)
        # The coordinator is Authority-bound (identity verified).
        assert runtime.session.coordinator.expected_identity is not None
        assert list((tmp_path / "authority").glob("*.authority.json"))
    finally:
        runtime.close()


def test_shared_mode_missing_authority_fails_closed_no_bootstrap(tmp_path: Path):
    # P1-2: normal runtime must NOT auto-bootstrap a missing Authority.
    from qmt_execution_core import AccountRuntimeAuthority
    from qmt_execution_core.exceptions import RuntimeConfigurationError

    qmt_path = tmp_path / "userdata_mini"
    qmt_path.mkdir()
    binding = _binding(tmp_path, qmt_path)
    config = _config(tmp_path, qmt_path, binding, strategy="a")
    store = AccountRuntimeAuthority(tmp_path / "authority")
    with pytest.raises(RuntimeConfigurationError):
        _connect(config, lambda path, sid: FakeTrader(path, sid), authority=store)
    # No Authority / coordination DB was created by the failed runtime.
    assert not list((tmp_path / "authority").glob("*.authority.json"))
    assert not list((tmp_path / "authority").glob("*.coordination.db"))


def test_shared_mode_corrupt_authority_fails_closed_no_fallback(tmp_path: Path):
    # Fail closed on a corrupt Authority; no fallback domain is created.
    from qmt_execution_core import AccountRuntimeAuthority
    from qmt_execution_core.exceptions import RuntimeConfigurationError

    qmt_path = tmp_path / "userdata_mini"
    qmt_path.mkdir()
    binding = _binding(tmp_path, qmt_path)
    config = _config(tmp_path, qmt_path, binding, strategy="a")
    store = AccountRuntimeAuthority(tmp_path / "authority")
    binding_payload = json.loads(binding.read_text(encoding="utf-8"))
    account_key = account_key_from_binding_identity(
        environment="simulation", account_type=2,
        account_id_sha256=binding_payload["account_id_sha256"],
    )
    (tmp_path / "authority").mkdir(parents=True, exist_ok=True)
    (tmp_path / "authority" / f"{account_key}.authority.json").write_text(
        "{broken", encoding="utf-8"
    )
    with pytest.raises(RuntimeConfigurationError):
        _connect(config, lambda path, sid: FakeTrader(path, sid), authority=store)
    assert not list((tmp_path / "authority").glob("*.coordination.db"))


def test_two_shared_runtimes_same_qmt_path_different_symbols_coexist(tmp_path: Path):
    qmt_path = tmp_path / "userdata_mini"
    qmt_path.mkdir()
    binding = _binding(tmp_path, qmt_path)
    coordination = tmp_path / "coord.db"
    holders = {}

    def factory_a(path, sid):
        holders["a"] = FakeTrader(path, sid)
        return holders["a"]

    def factory_b(path, sid):
        holders["b"] = FakeTrader(path, sid)
        return holders["b"]

    a = _connect(
        _config(tmp_path, qmt_path, binding, strategy="a"),
        factory_a,
        coordinator=SQLiteExecutionCoordinator(coordination),
    )
    b = _connect(
        _config(tmp_path, qmt_path, binding, strategy="b"),
        factory_b,
        coordinator=SQLiteExecutionCoordinator(coordination),
    )
    try:
        assert a.session_id != b.session_id
        assert a.submit(_request("a", "a1", "0700.HK")).state is TradeState.WORKING
        assert b.submit(_request("b", "b1", "510300.SH")).state is TradeState.WORKING
        assert holders["a"].place_calls == 1
        assert holders["b"].place_calls == 1
    finally:
        b.close()
        a.close()


def test_same_account_same_symbol_second_runtime_is_rejected_before_broker(tmp_path: Path):
    qmt_path = tmp_path / "userdata_mini"
    qmt_path.mkdir()
    binding = _binding(tmp_path, qmt_path)
    coordination = tmp_path / "coord.db"
    coordinator = SQLiteExecutionCoordinator(coordination)
    holders = {}

    def factory(name):
        def build(path, sid):
            holders[name] = FakeTrader(path, sid)
            return holders[name]
        return build

    a = _connect(
        _config(tmp_path, qmt_path, binding, strategy="a"),
        factory("a"),
        coordinator=coordinator,
    )
    b = _connect(
        _config(tmp_path, qmt_path, binding, strategy="b"),
        factory("b"),
        coordinator=coordinator,
    )
    try:
        assert a.submit(_request("a", "a1", "0700.HK")).state is TradeState.WORKING
        out = b.submit(_request("b", "b1", "0700.HK"))
        assert out.state is TradeState.REJECTED
        assert holders["b"].place_calls == 0

        account_key = account_key_from_binding_identity(
            environment="simulation",
            account_type=2,
            account_id_sha256=account_id_fingerprint("A123"),
        )
        claim = coordinator.get_claim(account_key, "0700.HK")
        assert claim is not None
        assert claim.execution_id == "a"
        assert claim.client_order_id == "a1"
    finally:
        b.close()
        a.close()


def test_same_symbol_different_accounts_are_independent(tmp_path: Path):
    qmt_path = tmp_path / "userdata_mini"
    qmt_path.mkdir()
    binding_a = _binding(tmp_path, qmt_path, account_id="A123", name="a-binding.json")
    binding_b = _binding(tmp_path, qmt_path, account_id="B456", name="b-binding.json")
    coordination = tmp_path / "coord.db"
    holders = {}

    def factory_a(path, sid):
        holders["a"] = FakeTrader(path, sid, account_id="A123")
        return holders["a"]

    def factory_b(path, sid):
        holders["b"] = FakeTrader(path, sid, account_id="B456")
        return holders["b"]

    a = _connect(
        _config(tmp_path, qmt_path, binding_a, strategy="a"),
        factory_a,
        coordinator=SQLiteExecutionCoordinator(coordination),
    )
    b = _connect(
        _config(tmp_path, qmt_path, binding_b, strategy="b"),
        factory_b,
        coordinator=SQLiteExecutionCoordinator(coordination),
    )
    try:
        assert a.submit(_request("a", "a1", "0700.HK")).state is TradeState.WORKING
        assert b.submit(_request("b", "b1", "0700.HK")).state is TradeState.WORKING
        assert holders["a"].place_calls == 1
        assert holders["b"].place_calls == 1
    finally:
        b.close()
        a.close()


def test_coordinated_buy_without_estimator_fails_before_broker(tmp_path: Path):
    qmt_path = tmp_path / "userdata_mini"
    qmt_path.mkdir()
    binding = _binding(tmp_path, qmt_path)
    coordination = tmp_path / "coord.db"
    holder = {}

    def factory(path, sid):
        holder["trader"] = FakeTrader(path, sid)
        return holder["trader"]

    runtime = _connect(
        _config(tmp_path, qmt_path, binding, strategy="a"),
        factory,
        estimator=False,
        coordinator=SQLiteExecutionCoordinator(coordination),
    )
    try:
        out = runtime.submit(_request("a", "a1", "0700.HK"))
        assert out.state is TradeState.REJECTED
        assert holder["trader"].place_calls == 0
    finally:
        runtime.close()


def test_exact_shared_session_id_is_exclusive_lease(tmp_path: Path):
    qmt_path = tmp_path / "userdata_mini"
    qmt_path.mkdir()
    binding = _binding(tmp_path, qmt_path)
    coordination = tmp_path / "coord.db"
    exact = 220_000_001
    a_cfg = _config(
        tmp_path,
        qmt_path,
        binding,
        strategy="a",
        session_id=exact,
    )
    b_cfg = replace(
        _config(
            tmp_path,
            qmt_path,
            binding,
            strategy="b",
        ),
        session_id=exact,
    )
    coordinator = SQLiteExecutionCoordinator(coordination)
    a = _connect(a_cfg, lambda path, sid: FakeTrader(path, sid),
                 coordinator=coordinator)
    try:
        with pytest.raises(SessionIdUnavailable):
            _connect(b_cfg, lambda path, sid: FakeTrader(path, sid),
                     coordinator=coordinator)
    finally:
        a.close()

    # Lease is OS/file-lock lifetime based and becomes reusable after close.
    b = _connect(b_cfg, lambda path, sid: FakeTrader(path, sid),
                 coordinator=coordinator)
    assert b.session_id == exact
    b.close()


def test_automatic_shared_session_id_connect_failure_has_finite_fallback(tmp_path: Path):
    qmt_path = tmp_path / "userdata_mini"
    qmt_path.mkdir()
    binding = _binding(tmp_path, qmt_path)
    coordination = tmp_path / "coord.db"
    tried = []

    def factory(path, sid):
        tried.append(sid)
        return FakeTrader(path, sid, connect_result=1 if len(tried) == 1 else 0)

    config = replace(
        _config(
            tmp_path,
            qmt_path,
            binding,
            strategy="fallback",
        ),
        session_id_pool_size=4,
        session_id_attempts=2,
    )
    runtime = _connect(config, factory,
                       coordinator=SQLiteExecutionCoordinator(coordination))
    try:
        assert len(tried) == 2
        assert tried[0] != tried[1]
        assert runtime.session_id == tried[1]
    finally:
        runtime.close()
