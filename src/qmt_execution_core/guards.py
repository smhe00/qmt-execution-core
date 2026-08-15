from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .domain import ExecutionRequest, PrecheckEvidence, SessionEvidence
from .ports import ExecutionGuard


@dataclass(frozen=True)
class ExecutionLimits:
    allowlist: frozenset[str]
    max_order_qty: int
    max_order_notional: float

    def __post_init__(self) -> None:
        if not isinstance(self.allowlist, frozenset) or not self.allowlist:
            raise ValueError("allowlist must be a non-empty frozenset")
        if any(type(symbol) is not str or not symbol for symbol in self.allowlist):
            raise ValueError("allowlist symbols must be non-empty strings")
        if type(self.max_order_qty) is not int or self.max_order_qty <= 0:
            raise ValueError("max_order_qty must be a positive plain int")
        if type(self.max_order_notional) not in (int, float) or isinstance(
            self.max_order_notional, bool
        ):
            raise ValueError("max_order_notional must be numeric")
        if not isfinite(float(self.max_order_notional)) or self.max_order_notional <= 0:
            raise ValueError("max_order_notional must be finite and positive")


class LimitExecutionGuard:
    """Compose hard execution limits with a project-provided evidence guard.

    Strategy-specific portfolio rules remain in the inner guard. This wrapper
    provides broker-independent allowlist/quantity/notional limits and a local
    kill switch before expensive precheck work or any broker side effect.
    """

    def __init__(self, inner: ExecutionGuard, limits: ExecutionLimits) -> None:
        self.inner = inner
        self.limits = limits
        self._kill_switch = False

    @property
    def kill_switch(self) -> bool:
        return self._kill_switch

    def engage_kill_switch(self) -> None:
        self._kill_switch = True

    def verify_session(self) -> SessionEvidence:
        return self.inner.verify_session()

    def verify(self, request: ExecutionRequest) -> PrecheckEvidence:
        if self._kill_switch:
            return _rejected("kill switch engaged")
        if request.symbol not in self.limits.allowlist:
            return _rejected("symbol is not on the execution allowlist")
        if request.qty > self.limits.max_order_qty:
            return _rejected("order quantity exceeds hard execution limit")
        notional = request.qty * float(request.limit_price)
        if notional > self.limits.max_order_notional:
            return _rejected("order notional exceeds hard execution limit")
        return self.inner.verify(request)


def _rejected(reason: str) -> PrecheckEvidence:
    return PrecheckEvidence(
        allowed=False,
        environment_verified=False,
        account_verified=False,
        broker_snapshot_verified=False,
        position_verified=False,
        cash_verified=False,
        quote_verified=False,
        reason=reason,
    )
