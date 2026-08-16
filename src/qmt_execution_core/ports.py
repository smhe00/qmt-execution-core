from __future__ import annotations

from typing import Protocol

from .domain import (
    BrokerAsset,
    BrokerOrder,
    CancelRequestResult,
    ExecutionRequest,
    PrecheckEvidence,
    SessionEvidence,
)


class BrokerPort(Protocol):
    def place_order(self, request: ExecutionRequest) -> int:
        ...

    def cancel_order(self, order_id: int) -> CancelRequestResult:
        ...

    def query_order(self, order_id: int) -> BrokerOrder:
        ...

    def query_orders(self) -> tuple[BrokerOrder, ...]:
        ...

    def execution_healthy(self) -> bool:
        ...


class AccountResourcePort(Protocol):
    """Broker/account facts used by shared-resource coordination.

    Kept separate from BrokerPort so existing fake/sim order brokers do not
    need to implement account-resource queries unless coordinated BUY
    execution is enabled.
    """

    def query_asset(self) -> BrokerAsset:
        ...


class ExecutionGuard(Protocol):
    def verify_session(self) -> SessionEvidence:
        ...

    def verify(self, request: ExecutionRequest) -> PrecheckEvidence:
        ...
