from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from qmt_execution_core import (
    ExecutionRequest,
    PrecheckEvidence,
    SessionEvidence,
    Side,
    TradeState,
)
from qmt_execution_core.exceptions import (
    AccountBindingError,
    BrokerSubmissionRejected,
)
from qmt_execution_core.journal import JournalIntegrityError
from qmt_execution_core.miniqmt import (
    MiniQmtRuntime,
    MiniQmtRuntimeConfig,
    QmtAccountBinding,
    token_sha256,
)
from qmt_execution_core.exceptions import RuntimeConfirmationError


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
        order_id=101,
        status=50,
        filled=0,
        qty=100,
        remark="demo_1",
        strategy_name="demo",
    ):
        self.order_id = order_id
        self.stock_code = "510300.SH"
        self.order_type = 23
        self.order_volume = qty
        self.traded_volume = filled
        self.order_status = status
        self.order_remark = remark
        self.strategy_name = strategy_name
        self.order_sysid = "sys-1"
        self.status_msg = ""
        self.traded_price = 4.7


class FakeTrader:
    def __init__(self, path: str, session_id: int):
        self.path = path
        self.session_id = session_id
        self.callback = None
        self.started = False
        self.stopped = False
        self.connect_result = 0
        self.subscribe_result = 0
        self.unsubscribe_result = 0
        self.account_id = "A123"
        self.account_type = 2
        self.account_status = 0
        self.place_calls = 0
        self.order = None
        self.cancel_result = 0

    def register_callback(self, callback):
        self.callback = callback

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def connect(self):
        return self.connect_result

    def subscribe(self, account):
        return self.subscribe_result

    def unsubscribe(self, account):
        return self.unsubscribe_result

    def query_account_infos(self):
        return [SimpleNamespace(account_id=self.account_id, account_type=self.account_type)]

    def query_account_status(self):
        return [
            SimpleNamespace(
                account_id=self.account_id,
                account_type=self.account_type,
                status=self.account_status,
            )
        ]

    def order_stock(self, account, symbol, order_type, qty, price_type, price, strategy, remark):
        self.place_calls += 1
        self.order = RawOrder(
            order_id=100 + self.place_calls,
            status=50,
            filled=0,
            qty=qty,
            remark=remark,
            strategy_name=strategy,
        )
        return self.order.order_id

    def cancel_order_stock(self, account, order_id):
        if self.cancel_result == 0 and self.order is not None:
            self.order.order_status = 51
        return self.cancel_result

    def query_stock_order(self, account, order_id):
        return self.order

    def query_stock_orders(self, account, cancelable_only=False):
        return [] if self.order is None else [self.order]

    def query_stock_asset(self, account):
        return SimpleNamespace(
            cash=100000.0,
            frozen_cash=100.0,
            market_value=200000.0,
            total_asset=300000.0,
        )

    def query_stock_positions(self, account):
        return [
            SimpleNamespace(
                stock_code="510300.SH",
                volume=1000,
                can_use_volume=900,
                frozen_volume=100,
                market_value=4700.0,
                avg_price=4.5,
            )
        ]

    def query_stock_trades(self, account):
        if self.order is None or self.order.traded_volume <= 0:
            return []
        return [
            SimpleNamespace(
                order_id=self.order.order_id,
                stock_code=self.order.stock_code,
                order_type=23,
                traded_volume=self.order.traded_volume,
                traded_price=self.order.traded_price,
                traded_amount=self.order.traded_volume * self.order.traded_price,
                traded_id="T1",
                strategy_name=self.order.strategy_name,
                order_remark=self.order.order_remark,
            )
        ]


def make_runtime(tmp_path: Path, *, environment="simulation", live_enabled=False, token="secret"):
    qmt_path = tmp_path / "userdata_mini"
    qmt_path.mkdir()
    binding_path = tmp_path / "binding.json"
    QmtAccountBinding.create(
        environment=environment,
        account_type=2,
        account_id="A123",
        qmt_path=qmt_path,
    ).write(binding_path)
    config = MiniQmtRuntimeConfig(
        environment=environment,
        qmt_path=qmt_path,
        binding_path=binding_path,
        journal_path=tmp_path / "journal.json",
        lock_path=tmp_path / "exec.lock",
        strategy_name="demo",
        live_trading_enabled=live_enabled,
        confirmation_token_sha256=token_sha256(token) if live_enabled else "",
        query_delay_seconds=0,
    )
    holder = {}

    def factory(path, session_id):
        trader = FakeTrader(path, session_id)
        holder["trader"] = trader
        return trader

    runtime = MiniQmtRuntime.connect(
        config,
        guard=AllowGuard(),
        trader_factory=factory,
        stock_account_factory=StockAccount,
        xtconstant=XtConstant,
        callback_base=CallbackBase,
    )
    return runtime, holder["trader"]


def request(client_order_id="c1", remark="demo_1"):
    return ExecutionRequest(
        client_order_id,
        "510300.SH",
        Side.BUY,
        100,
        4.7,
        "demo",
        remark,
    )


def wait_until(predicate, timeout=1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


def test_simulation_runtime_connects_and_executes(tmp_path):
    runtime, trader = make_runtime(tmp_path)
    assert runtime.execution_healthy
    out = runtime.submit(request())
    assert out.state is TradeState.WORKING
    assert trader.place_calls == 1
    runtime.close()
    assert trader.stopped


def test_live_requires_config_and_runtime_confirmation(tmp_path):
    runtime, trader = make_runtime(tmp_path, environment="live", live_enabled=True)
    assert not runtime.execution_healthy
    with pytest.raises(BrokerSubmissionRejected):
        runtime.submit(request())
    assert trader.place_calls == 0
    with pytest.raises(RuntimeConfirmationError):
        runtime.confirm_live("wrong")
    runtime.confirm_live("secret")
    assert runtime.execution_healthy
    assert runtime.submit(request()).state is TradeState.WORKING
    runtime.close()


def test_live_disabled_cannot_confirm(tmp_path):
    runtime, trader = make_runtime(tmp_path, environment="live", live_enabled=False)
    assert not runtime.execution_healthy
    with pytest.raises(RuntimeConfirmationError):
        runtime.confirm_live("secret")
    with pytest.raises(BrokerSubmissionRejected):
        runtime.submit(request())
    assert trader.place_calls == 0
    runtime.close()


def test_disconnect_revokes_execution_and_recovery_requires_full_sequence(tmp_path):
    runtime, trader = make_runtime(tmp_path, environment="live", live_enabled=True)
    runtime.confirm_live("secret")
    out = runtime.submit(request())
    assert out.state is TradeState.WORKING

    trader.callback.on_disconnected()
    wait_until(lambda: not runtime.execution_healthy)
    assert not runtime.execution_healthy

    with pytest.raises(Exception):
        runtime.recover_after_disconnect()

    assert not runtime.execution_healthy
    runtime.recover_after_disconnect(runtime_token="secret")
    assert runtime.execution_healthy
    runtime.close()


def test_account_status_callback_only_invalidates_never_restores(tmp_path):
    runtime, trader = make_runtime(tmp_path)
    assert runtime.execution_healthy
    trader.callback.on_account_status(
        SimpleNamespace(account_id="A123", account_type=2, status=3)
    )
    wait_until(lambda: not runtime.execution_healthy)

    trader.callback.on_account_status(
        SimpleNamespace(account_id="A123", account_type=2, status=0)
    )
    time.sleep(0.05)
    assert not runtime.execution_healthy
    runtime.recover_after_disconnect()
    assert runtime.execution_healthy
    runtime.close()


def test_wrong_bound_account_status_fails_connect(tmp_path):
    qmt_path = tmp_path / "userdata_mini"
    qmt_path.mkdir()
    binding = tmp_path / "binding.json"
    QmtAccountBinding.create(
        environment="simulation",
        account_type=2,
        account_id="A123",
        qmt_path=qmt_path,
    ).write(binding)
    config = MiniQmtRuntimeConfig(
        environment="simulation",
        qmt_path=qmt_path,
        binding_path=binding,
        journal_path=tmp_path / "j.json",
        lock_path=tmp_path / "l.lock",
        strategy_name="demo",
        query_delay_seconds=0,
    )

    def factory(path, session_id):
        t = FakeTrader(path, session_id)
        t.account_status = 3
        return t

    with pytest.raises(AccountBindingError):
        MiniQmtRuntime.connect(
            config,
            guard=AllowGuard(),
            trader_factory=factory,
            stock_account_factory=StockAccount,
            xtconstant=XtConstant,
            callback_base=CallbackBase,
        )


def test_cross_cycle_client_id_and_remark_are_durable_idempotency_keys(tmp_path):
    runtime, trader = make_runtime(tmp_path)
    out = runtime.submit(request())
    trader.order.order_status = 56
    trader.order.traded_volume = 100
    assert runtime.poll().state is TradeState.FILLED
    assert runtime.next_cycle().state is TradeState.WAIT_TRIGGER

    with pytest.raises(JournalIntegrityError):
        runtime.submit(request())
    assert trader.place_calls == 1

    with pytest.raises(JournalIntegrityError):
        runtime.submit(request(client_order_id="c2", remark="demo_1"))
    assert trader.place_calls == 1
    runtime.close()


def test_adapter_data_queries_available_for_project_guards(tmp_path):
    runtime, trader = make_runtime(tmp_path)
    asset = runtime.broker.query_asset()
    assert asset.cash == 100000.0
    positions = runtime.broker.query_positions()
    assert positions[0].can_use_volume == 900

    runtime.submit(request())
    trader.order.order_status = 55
    trader.order.traded_volume = 40
    trader.order.traded_price = 4.69
    trades = runtime.broker.query_trades()
    assert trades[0].traded_volume == 40
    runtime.close()


def test_strategy_id_must_match_runtime_strategy_name(tmp_path):
    runtime, trader = make_runtime(tmp_path)
    bad = ExecutionRequest("c1", "510300.SH", Side.BUY, 100, 4.7, "other", "demo_1")
    with pytest.raises(Exception):
        runtime.submit(bad)
    assert trader.place_calls == 0
    runtime.close()


def test_runtime_config_json_requires_schema_version(tmp_path):
    import json

    payload = {
        "environment": "simulation",
        "qmt_path": "userdata",
        "binding_path": "binding.json",
        "journal_path": "journal.json",
        "lock_path": "exec.lock",
        "strategy_name": "demo",
    }
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    from qmt_execution_core.exceptions import RuntimeConfigurationError
    with pytest.raises(RuntimeConfigurationError):
        MiniQmtRuntimeConfig.from_json(path)

    payload["schema_version"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    cfg = MiniQmtRuntimeConfig.from_json(path)
    assert cfg.environment == "simulation"
    assert cfg.qmt_path == tmp_path / "userdata"


def test_qmt_order_constants_require_plain_ints():
    from qmt_execution_core.miniqmt.adapter import QmtOrderConfig

    bad = SimpleNamespace(STOCK_BUY=True, STOCK_SELL=24)
    with pytest.raises(TypeError):
        QmtOrderConfig.from_xtconstant(bad, price_type=11)


def test_same_qmt_path_allows_only_one_runtime_even_with_different_project_locks(tmp_path):
    from qmt_execution_core.mutex import ConcurrentExecutionError

    runtime, _ = make_runtime(tmp_path)
    second = replace(
        runtime.config,
        journal_path=tmp_path / "other-journal.json",
        lock_path=tmp_path / "other-project.lock",
    )

    with pytest.raises(ConcurrentExecutionError):
        MiniQmtRuntime.connect(
            second,
            guard=AllowGuard(),
            trader_factory=FakeTrader,
            stock_account_factory=StockAccount,
            xtconstant=XtConstant,
            callback_base=CallbackBase,
        )

    runtime.close()
    later = MiniQmtRuntime.connect(
        second,
        guard=AllowGuard(),
        trader_factory=FakeTrader,
        stock_account_factory=StockAccount,
        xtconstant=XtConstant,
        callback_base=CallbackBase,
    )
    assert later.execution_healthy
    later.close()
