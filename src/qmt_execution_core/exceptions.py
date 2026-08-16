class ExecutionCoreError(RuntimeError):
    """Base error for qmt-execution-core."""


class SessionClosedError(ExecutionCoreError):
    pass


class BrokerError(ExecutionCoreError):
    pass


class BrokerExecutionDisabled(BrokerError):
    """Local execution gate intentionally blocks new orders."""


class BrokerSubmissionRejected(BrokerError):
    """Broker/API definitively rejected a submit request."""


class BrokerSubmissionAmbiguous(BrokerError):
    """Submission may or may not have reached the broker."""


class BrokerQueryAmbiguous(BrokerError):
    """Authoritative broker state cannot be determined."""


class RecoveryAmbiguous(ExecutionCoreError):
    pass


class RuntimeConfigurationError(ExecutionCoreError):
    pass


class AccountBindingError(ExecutionCoreError):
    pass


class EventQueueUnhealthy(ExecutionCoreError):
    pass


class RuntimeConfirmationError(ExecutionCoreError):
    pass


class CoordinationError(ExecutionCoreError):
    """Shared-account coordination could not be completed safely."""


class SymbolClaimConflict(CoordinationError):
    """Another unresolved execution owns the same account/symbol claim."""


class CashReservationRejected(CoordinationError):
    """Fresh broker cash minus active reservations cannot fund the request."""


class SessionIdUnavailable(RuntimeConfigurationError):
    """No MiniQMT session id could be leased from the configured bounded pool."""
