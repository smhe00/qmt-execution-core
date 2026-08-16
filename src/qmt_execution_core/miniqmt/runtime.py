from __future__ import annotations

import json
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..coordinated_session import CoordinatedExecutionSession
from ..coordination import (
    CashRequirementEstimator,
    ExecutionCoordinator,
    SQLiteExecutionCoordinator,
    account_key_from_binding_identity,
)
from ..domain import ExecutionRequest, ExecutionSnapshot, TradeState
from ..event_queue import SerialEventQueue
from ..mutex import ExecutionMutex
from ..exceptions import (
    EventQueueUnhealthy,
    RecoveryAmbiguous,
    RuntimeConfigurationError,
    SessionIdUnavailable,
)
from ..ports import ExecutionGuard
from ..session import ExecutionSession
from .adapter import MiniQmtBrokerAdapter, QmtOrderConfig
from .binding import (
    BoundQmtAccount,
    load_account_binding,
    qmt_path_fingerprint,
    select_bound_account,
    verify_bound_account_healthy,
)
from .callbacks import (
    QmtAccountStatusObserved,
    QmtBrokerDisconnected,
    QmtCallbackBridge,
    QmtCallbackMalformed,
)
from .runtime_gate import RuntimeExecutionGate, RuntimeGateConfig
from .session_id import BoundedSessionIdAllocator, SessionIdLease


_ALLOWED_ENVIRONMENTS = {"simulation", "live"}
_ALLOWED_RUNTIME_LOCK_MODES = {"exclusive", "shared"}
_RUNTIME_CONFIG_SCHEMA_VERSION = 1
_RUNTIME_REQUIRED_FIELDS = {
    "schema_version",
    "environment",
    "qmt_path",
    "binding_path",
    "journal_path",
    "lock_path",
    "strategy_name",
}
_RUNTIME_OPTIONAL_FIELDS = {
    "live_trading_enabled",
    "confirmation_token_sha256",
    "session_id",
    "query_attempts",
    "query_delay_seconds",
    "event_queue_size",
    "runtime_lock_mode",
    "coordination_path",
    "session_id_pool_start",
    "session_id_pool_size",
    "session_id_attempts",
}
_RUNTIME_CONFIG_FIELDS = _RUNTIME_REQUIRED_FIELDS | _RUNTIME_OPTIONAL_FIELDS


@dataclass(frozen=True)
class MiniQmtRuntimeConfig:
    environment: str
    qmt_path: Path | str
    binding_path: Path | str
    journal_path: Path | str
    lock_path: Path | str
    strategy_name: str
    live_trading_enabled: bool = False
    confirmation_token_sha256: str = ""
    session_id: int | None = None
    query_attempts: int = 3
    query_delay_seconds: float = 0.15
    event_queue_size: int = 1024
    runtime_lock_mode: str = "exclusive"
    coordination_path: Path | str | None = None
    session_id_pool_start: int = 100_000_000
    session_id_pool_size: int = 1_000
    session_id_attempts: int = 32

    def __post_init__(self) -> None:
        if self.environment not in _ALLOWED_ENVIRONMENTS:
            raise RuntimeConfigurationError("environment must be simulation or live")
        for name in ("qmt_path", "binding_path", "journal_path", "lock_path"):
            value = Path(getattr(self, name)).expanduser()
            object.__setattr__(self, name, value)
        if self.coordination_path is not None:
            object.__setattr__(
                self,
                "coordination_path",
                Path(self.coordination_path).expanduser(),
            )
        if type(self.strategy_name) is not str or not self.strategy_name:
            raise RuntimeConfigurationError("strategy_name must be non-empty")
        if type(self.live_trading_enabled) is not bool:
            raise RuntimeConfigurationError("live_trading_enabled must be bool")
        if self.session_id is not None and (
            type(self.session_id) is not int or self.session_id <= 0
        ):
            raise RuntimeConfigurationError("session_id must be a positive int or None")
        if type(self.query_attempts) is not int or self.query_attempts <= 0:
            raise RuntimeConfigurationError("query_attempts must be positive")
        if type(self.query_delay_seconds) not in (int, float) or isinstance(
            self.query_delay_seconds, bool
        ):
            raise RuntimeConfigurationError("query_delay_seconds must be numeric")
        if float(self.query_delay_seconds) < 0:
            raise RuntimeConfigurationError("query_delay_seconds cannot be negative")
        if type(self.event_queue_size) is not int or self.event_queue_size <= 0:
            raise RuntimeConfigurationError("event_queue_size must be positive")
        if self.runtime_lock_mode not in _ALLOWED_RUNTIME_LOCK_MODES:
            raise RuntimeConfigurationError(
                "runtime_lock_mode must be exactly 'exclusive' or 'shared'"
            )
        if type(self.session_id_pool_start) is not int or self.session_id_pool_start <= 0:
            raise RuntimeConfigurationError("session_id_pool_start must be positive")
        if type(self.session_id_pool_size) is not int or self.session_id_pool_size <= 0:
            raise RuntimeConfigurationError("session_id_pool_size must be positive")
        if type(self.session_id_attempts) is not int or self.session_id_attempts <= 0:
            raise RuntimeConfigurationError("session_id_attempts must be positive")
        if self.session_id_pool_start + self.session_id_pool_size - 1 > 2_147_483_647:
            raise RuntimeConfigurationError("session id pool exceeds signed 32-bit range")

        RuntimeGateConfig(
            environment=self.environment,
            live_trading_enabled=self.live_trading_enabled,
            confirmation_token_sha256=self.confirmation_token_sha256,
        )

    @classmethod
    def from_json(cls, path: Path | str) -> "MiniQmtRuntimeConfig":
        target = Path(path)
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeConfigurationError("runtime config is unreadable") from exc
        if not isinstance(payload, dict):
            raise RuntimeConfigurationError("runtime config must be a JSON object")
        keys = set(payload)
        if not _RUNTIME_REQUIRED_FIELDS <= keys or not keys <= _RUNTIME_CONFIG_FIELDS:
            raise RuntimeConfigurationError("runtime config fields do not match strict schema")
        if payload.pop("schema_version", None) != _RUNTIME_CONFIG_SCHEMA_VERSION:
            raise RuntimeConfigurationError("runtime config schema_version mismatch")
        base = target.parent
        for name in ("qmt_path", "binding_path", "journal_path", "lock_path"):
            value = Path(str(payload[name])).expanduser()
            if not value.is_absolute():
                value = base / value
            payload[name] = value
        if payload.get("coordination_path") is not None:
            value = Path(str(payload["coordination_path"])).expanduser()
            if not value.is_absolute():
                value = base / value
            payload["coordination_path"] = value
        return cls(**payload)


@dataclass
class _RuntimeSignals:
    transport_connected: bool = False
    account_healthy: bool = False


class MiniQmtRuntime:
    """Production-shaped reusable MiniQMT runtime.

    ``exclusive`` mode preserves the v0.3 qmt-path-wide safety mutex.
    ``shared`` mode replaces that coarse exclusion with durable per-symbol/
    shared-cash coordination plus an OS-lock-backed bounded MiniQMT session id.
    """

    def __init__(
        self,
        *,
        config: MiniQmtRuntimeConfig,
        trader: object,
        bound_account: BoundQmtAccount,
        broker: MiniQmtBrokerAdapter,
        session: ExecutionSession,
        event_queue: SerialEventQueue,
        runtime_mutex: ExecutionMutex | None,
        session_id_lease: SessionIdLease | None,
        session_id: int,
        gate: RuntimeExecutionGate,
        signals: _RuntimeSignals,
        security_account_type: int,
        account_status_ok: int,
        observation_handler: Callable[[object], None] | None = None,
    ) -> None:
        self.config = config
        self.trader = trader
        self.bound_account = bound_account
        self.broker = broker
        self.session = session
        self.event_queue = event_queue
        self.runtime_mutex = runtime_mutex
        self.session_id_lease = session_id_lease
        self.session_id = session_id
        self.gate = gate
        self._signals = signals
        self._security_account_type = security_account_type
        self._account_status_ok = account_status_ok
        self._observation_handler = observation_handler
        self._closed = False

    @classmethod
    def connect(
        cls,
        config: MiniQmtRuntimeConfig,
        *,
        guard: ExecutionGuard,
        observation_handler: Callable[[object], None] | None = None,
        trader_factory: Callable[[str, int], object] | None = None,
        stock_account_factory: Callable[[str], object] | None = None,
        xtconstant: object | None = None,
        callback_base: type | None = None,
        auto_open: bool = True,
        before_broker_submit: Callable[[ExecutionRequest], None] | None = None,
        before_broker_cancel: Callable[[int], None] | None = None,
        coordinator: ExecutionCoordinator | None = None,
        cash_estimator: CashRequirementEstimator | None = None,
    ) -> "MiniQmtRuntime":
        if not isinstance(config, MiniQmtRuntimeConfig):
            raise RuntimeConfigurationError("config must be MiniQmtRuntimeConfig")
        qmt_path = Path(config.qmt_path).resolve(strict=False)
        if not qmt_path.is_dir():
            raise RuntimeConfigurationError(f"QMT userdata path does not exist: {qmt_path}")

        if any(
            item is None
            for item in (trader_factory, stock_account_factory, xtconstant, callback_base)
        ):
            real = _real_xtquant_dependencies()
            trader_factory = trader_factory or real["trader_factory"]
            stock_account_factory = stock_account_factory or real["stock_account_factory"]
            xtconstant = xtconstant or real["xtconstant"]
            callback_base = callback_base or real["callback_base"]

        assert trader_factory is not None
        assert stock_account_factory is not None
        assert xtconstant is not None
        assert callback_base is not None

        security_account_type = _exact_int_attr(xtconstant, "SECURITY_ACCOUNT")
        account_status_ok = _exact_int_attr(xtconstant, "ACCOUNT_STATUS_OK")
        price_type = _exact_int_attr(xtconstant, "FIX_PRICE")

        binding = load_account_binding(
            config.binding_path,
            environment=config.environment,
            qmt_path=qmt_path,
        )
        if coordinator is None and config.coordination_path is not None:
            coordinator = SQLiteExecutionCoordinator(config.coordination_path)
        if config.runtime_lock_mode == "shared" and coordinator is None:
            raise RuntimeConfigurationError(
                "shared runtime mode requires coordination_path or injected coordinator"
            )
        account_key = account_key_from_binding_identity(
            environment=binding.environment,
            account_type=binding.account_type,
            account_id_sha256=binding.account_id_sha256,
        )

        gate = RuntimeExecutionGate(
            RuntimeGateConfig(
                environment=config.environment,
                live_trading_enabled=config.live_trading_enabled,
                confirmation_token_sha256=config.confirmation_token_sha256,
            )
        )
        signals = _RuntimeSignals()
        holder: dict[str, object] = {}

        def handle_callback(event: object) -> None:
            runtime = holder.get("runtime")
            if isinstance(event, QmtCallbackMalformed):
                raise EventQueueUnhealthy(
                    f"malformed broker callback {event.callback_name}: {event.error}"
                )
            if isinstance(event, QmtBrokerDisconnected):
                signals.transport_connected = False
                signals.account_healthy = False
                gate.revoke()
                if runtime is not None:
                    runtime.broker.mark_disconnected()  # type: ignore[attr-defined]
            elif isinstance(event, QmtAccountStatusObserved) and runtime is not None:
                bound = runtime.bound_account  # type: ignore[attr-defined]
                if (
                    event.account_id == bound.account_id
                    and (
                        event.account_type != security_account_type
                        or event.status != account_status_ok
                    )
                ):
                    signals.account_healthy = False
                    gate.revoke()
                    runtime.broker.mark_recovery_required()  # type: ignore[attr-defined]
            if observation_handler is not None:
                observation_handler(event)

        event_queue = SerialEventQueue(handle_callback, maxsize=config.event_queue_size)
        bridge = QmtCallbackBridge(event_queue.try_emit)
        callback = _make_callback(callback_base, bridge)

        runtime_mutex: ExecutionMutex | None = None
        session_id_lease: SessionIdLease | None = None
        trader = None
        session: ExecutionSession | None = None
        actual_session_id: int | None = None

        try:
            if config.runtime_lock_mode == "exclusive":
                runtime_mutex = ExecutionMutex(_runtime_mutex_path(qmt_path))
                runtime_mutex.acquire()

            event_queue.start()

            if config.runtime_lock_mode == "shared":
                allocator = BoundedSessionIdAllocator(
                    qmt_path,
                    pool_start=config.session_id_pool_start,
                    pool_size=config.session_id_pool_size,
                    attempts=config.session_id_attempts,
                )
                if config.session_id is not None:
                    candidate_ids = (config.session_id,)
                else:
                    candidate_ids = allocator.candidate_ids(
                        f"{config.environment}|{config.strategy_name}"
                    )

                last_error: Exception | None = None
                for candidate in candidate_ids:
                    lease: SessionIdLease | None = None
                    candidate_trader = None
                    try:
                        lease = allocator.acquire_exact(candidate)
                        candidate_trader = trader_factory(str(qmt_path), candidate)
                        register = getattr(candidate_trader, "register_callback", None)
                        if not callable(register):
                            raise RuntimeConfigurationError(
                                "XtQuant trader has no register_callback"
                            )
                        register(callback)
                        getattr(candidate_trader, "start")()
                        _require_exact_zero(
                            getattr(candidate_trader, "connect")(),
                            "trader.connect",
                        )
                    except Exception as exc:
                        last_error = exc
                        if candidate_trader is not None:
                            _best_effort_stop(candidate_trader)
                        if lease is not None:
                            lease.release()
                        if config.session_id is not None:
                            raise
                        continue
                    trader = candidate_trader
                    session_id_lease = lease
                    actual_session_id = candidate
                    break
                if trader is None or session_id_lease is None or actual_session_id is None:
                    raise SessionIdUnavailable(
                        "shared runtime exhausted bounded session-id attempts"
                    ) from last_error
            else:
                actual_session_id = (
                    config.session_id
                    or secrets.randbelow(900_000_000) + 100_000_000
                )
                trader = trader_factory(str(qmt_path), actual_session_id)
                register = getattr(trader, "register_callback", None)
                if not callable(register):
                    raise RuntimeConfigurationError("XtQuant trader has no register_callback")
                register(callback)
                getattr(trader, "start")()
                _require_exact_zero(getattr(trader, "connect")(), "trader.connect")

            signals.transport_connected = True

            bound = select_bound_account(
                trader,
                binding=binding,
                security_account_type=security_account_type,
                account_status_ok=account_status_ok,
                stock_account_factory=stock_account_factory,
                attempts=config.query_attempts,
                delay_seconds=config.query_delay_seconds,
            )
            _require_exact_zero(
                getattr(trader, "subscribe")(bound.account),
                "trader.subscribe",
            )
            signals.account_healthy = True

            order_config = QmtOrderConfig.from_xtconstant(
                xtconstant,
                price_type=price_type,
            )
            broker = MiniQmtBrokerAdapter(
                trader,
                bound.account,
                order_config=order_config,
                strategy_name=config.strategy_name,
                query_attempts=config.query_attempts,
                query_delay_seconds=config.query_delay_seconds,
                health_probe=lambda: (
                    event_queue.healthy
                    and signals.transport_connected
                    and signals.account_healthy
                ),
                order_gate=lambda: gate.execution_allowed,
                initially_connected=True,
                recovery_ready=False,
            )

            if coordinator is not None:
                session = CoordinatedExecutionSession(
                    broker=broker,
                    guard=guard,
                    journal_path=config.journal_path,
                    lock_path=config.lock_path,
                    coordinator=coordinator,
                    account_key=account_key,
                    account_resource=broker,
                    cash_estimator=cash_estimator,
                    execution_id=config.strategy_name,
                    before_broker_submit=before_broker_submit,
                    before_broker_cancel=before_broker_cancel,
                )
            else:
                session = ExecutionSession(
                    broker=broker,
                    guard=guard,
                    journal_path=config.journal_path,
                    lock_path=config.lock_path,
                    execution_id=config.strategy_name,
                    before_broker_submit=before_broker_submit,
                    before_broker_cancel=before_broker_cancel,
                )

            runtime = cls(
                config=config,
                trader=trader,
                bound_account=bound,
                broker=broker,
                session=session,
                event_queue=event_queue,
                runtime_mutex=runtime_mutex,
                session_id_lease=session_id_lease,
                session_id=actual_session_id,
                gate=gate,
                signals=signals,
                security_account_type=security_account_type,
                account_status_ok=account_status_ok,
                observation_handler=observation_handler,
            )
            holder["runtime"] = runtime
            if auto_open:
                runtime.open()
            return runtime
        except Exception:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
            event_queue.stop()
            event_queue.join(timeout=1.0)
            if trader is not None:
                _best_effort_stop(trader)
            if session_id_lease is not None:
                session_id_lease.release()
            if runtime_mutex is not None:
                runtime_mutex.release()
            raise

    def open(self) -> ExecutionSnapshot:
        self._require_not_closed()
        verify_bound_account_healthy(
            self.trader,
            self.bound_account,
            security_account_type=self._security_account_type,
            account_status_ok=self._account_status_ok,
            attempts=self.config.query_attempts,
            delay_seconds=self.config.query_delay_seconds,
        )
        out = self.session.open()
        self._signals.transport_connected = True
        self._signals.account_healthy = True
        self.broker.mark_transport_connected()
        self.broker.mark_recovery_complete()
        return out

    @property
    def execution_healthy(self) -> bool:
        return self.broker.execution_healthy()

    def confirm_live(self, token: str) -> None:
        self._require_not_closed()
        self.gate.confirm(token)

    def submit(self, request: ExecutionRequest) -> ExecutionSnapshot:
        self._require_not_closed()
        if request.strategy_id != self.config.strategy_name:
            raise RuntimeConfigurationError(
                "ExecutionRequest.strategy_id must equal the bound MiniQMT strategy_name"
            )
        return self.session.submit(request)

    def poll(self) -> ExecutionSnapshot:
        self._require_not_closed()
        return self.session.poll()

    def cancel(self) -> ExecutionSnapshot:
        self._require_not_closed()
        return self.session.cancel()

    def next_cycle(self) -> ExecutionSnapshot:
        self._require_not_closed()
        return self.session.next_cycle()

    def recover_after_disconnect(
        self,
        *,
        runtime_token: str | None = None,
    ) -> ExecutionSnapshot:
        """Restore transport, account, subscription and durable execution state.

        Transport reconnection alone never restores new-order capability.
        """

        self._require_not_closed()
        self.gate.revoke()
        self.broker.mark_recovery_required()
        self._signals.transport_connected = False
        self._signals.account_healthy = False

        if not self.event_queue.healthy:
            raise EventQueueUnhealthy("callback event queue is not healthy")

        _require_exact_zero(getattr(self.trader, "connect")(), "trader.connect")
        self._signals.transport_connected = True
        self.broker.mark_transport_connected()

        verify_bound_account_healthy(
            self.trader,
            self.bound_account,
            security_account_type=self._security_account_type,
            account_status_ok=self._account_status_ok,
            attempts=self.config.query_attempts,
            delay_seconds=self.config.query_delay_seconds,
        )
        _require_exact_zero(
            getattr(self.trader, "subscribe")(self.bound_account.account),
            "trader.subscribe",
        )
        self._signals.account_healthy = True

        out = self.session.reconcile()
        if out.state in {TradeState.UNKNOWN, TradeState.FAILED}:
            raise RecoveryAmbiguous(
                f"durable execution did not reconcile after reconnect: {out.state.value}"
            )

        evidence = self.session.guard.verify_session()
        evidence.validate()
        if not evidence.ready:
            raise RecoveryAmbiguous(evidence.reason or "session re-verification failed")

        self.broker.mark_recovery_complete()
        if self.config.environment == "live":
            if runtime_token is None:
                raise RecoveryAmbiguous(
                    "live reconnect requires a fresh runtime confirmation token"
                )
            self.gate.confirm(runtime_token)
        return out

    def close(self) -> None:
        if self._closed:
            return
        self.gate.revoke()
        self.broker.mark_disconnected()
        try:
            self.session.close()
        finally:
            try:
                unsubscribe = getattr(self.trader, "unsubscribe", None)
                if callable(unsubscribe):
                    unsubscribe(self.bound_account.account)
            finally:
                self.event_queue.stop()
                self.event_queue.join(timeout=1.0)
                _best_effort_stop(self.trader)
                if self.session_id_lease is not None:
                    self.session_id_lease.release()
                if self.runtime_mutex is not None:
                    self.runtime_mutex.release()
                self._closed = True

    def _require_not_closed(self) -> None:
        if self._closed:
            raise RuntimeError("MiniQmtRuntime is closed")


def _runtime_mutex_path(qmt_path: Path | str) -> Path:
    fingerprint = qmt_path_fingerprint(qmt_path)
    return (
        Path(tempfile.gettempdir())
        / "qmt-execution-core"
        / f"qmt-runtime-{fingerprint}.lock"
    )


def _make_callback(callback_base: type, bridge: QmtCallbackBridge) -> object:
    class RuntimeCallback(callback_base):  # type: ignore[misc,valid-type]
        def on_stock_order(self, order):
            bridge.on_stock_order(order)

        def on_stock_trade(self, trade):
            bridge.on_stock_trade(trade)

        def on_disconnected(self):
            bridge.on_disconnected()

        def on_account_status(self, status):
            bridge.on_account_status(status)

        def on_order_error(self, error):
            bridge.on_order_error(error)

        def on_cancel_error(self, error):
            bridge.on_cancel_error(error)

    return RuntimeCallback()


def _real_xtquant_dependencies() -> dict[str, object]:
    try:
        from xtquant import xtconstant
        from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
        from xtquant.xttype import StockAccount
    except ImportError as exc:
        raise RuntimeConfigurationError(
            "xtquant is not installed; MiniQmtRuntime requires the MiniQMT environment"
        ) from exc

    return {
        "xtconstant": xtconstant,
        "callback_base": XtQuantTraderCallback,
        "trader_factory": lambda path, session_id: XtQuantTrader(path, session_id),
        "stock_account_factory": lambda account_id: StockAccount(account_id),
    }


def _exact_int_attr(obj: object, name: str) -> int:
    value = getattr(obj, name, None)
    if type(value) is not int:
        raise RuntimeConfigurationError(f"xtconstant.{name} must be a plain int")
    return value


def _require_exact_zero(value: object, label: str) -> None:
    if type(value) is not int or value != 0:
        raise RuntimeConfigurationError(f"{label} did not return exact success 0")


def _best_effort_stop(trader: object) -> None:
    try:
        stop = getattr(trader, "stop", None)
        if callable(stop):
            stop()
    except BaseException:
        pass
