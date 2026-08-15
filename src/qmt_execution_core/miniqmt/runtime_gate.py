from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from ..exceptions import RuntimeConfigurationError, RuntimeConfirmationError


_ALLOWED_ENVIRONMENTS = {"simulation", "live"}


def token_sha256(token: str) -> str:
    if type(token) is not str or not token:
        raise RuntimeConfirmationError("runtime confirmation token must be non-empty")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RuntimeGateConfig:
    environment: str
    live_trading_enabled: bool = False
    confirmation_token_sha256: str = ""

    def __post_init__(self) -> None:
        if self.environment not in _ALLOWED_ENVIRONMENTS:
            raise RuntimeConfigurationError("environment must be simulation or live")
        if type(self.live_trading_enabled) is not bool:
            raise RuntimeConfigurationError("live_trading_enabled must be a plain bool")
        digest = self.confirmation_token_sha256.strip().lower()
        if digest and (len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
            raise RuntimeConfigurationError("confirmation_token_sha256 must be a SHA-256 digest")
        object.__setattr__(self, "confirmation_token_sha256", digest)
        if self.environment == "simulation" and self.live_trading_enabled:
            raise RuntimeConfigurationError("simulation cannot enable live trading")
        if self.environment == "live" and self.live_trading_enabled and not digest:
            raise RuntimeConfigurationError(
                "live trading enablement requires a non-persisted runtime token digest"
            )


class RuntimeExecutionGate:
    """Double gate for real-money execution.

    Simulation is ready once the broker/session is healthy. Live execution
    requires BOTH trusted configuration enablement and a runtime-only
    confirmation token. Confirmation is revoked on disconnect/teardown.
    """

    def __init__(self, config: RuntimeGateConfig) -> None:
        self.config = config
        self._confirmed = config.environment == "simulation"

    @property
    def confirmed(self) -> bool:
        return self._confirmed

    @property
    def execution_allowed(self) -> bool:
        if self.config.environment == "simulation":
            return True
        return self.config.live_trading_enabled and self._confirmed

    def confirm(self, token: str) -> None:
        if self.config.environment != "live":
            raise RuntimeConfirmationError("runtime confirmation applies only to live environment")
        if not self.config.live_trading_enabled:
            raise RuntimeConfirmationError("live trading is disabled by configuration")
        digest = token_sha256(token)
        if not hmac.compare_digest(digest, self.config.confirmation_token_sha256):
            raise RuntimeConfirmationError("runtime confirmation token mismatch")
        self._confirmed = True

    def revoke(self) -> None:
        if self.config.environment == "live":
            self._confirmed = False
