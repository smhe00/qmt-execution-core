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


class RuntimeAuthorityError(ExecutionCoreError):
    """Account Runtime Authority is missing, corrupt, or identity-mismatched.

    Core 0.4.1: shared execution for one account is allowed only after the
    canonical per-account Runtime Authority verifies the dedicated
    coordination DB instance (INV-AUTH-001 / INV-AUTH-002).
    """


class CoordinationIdentityError(CoordinationError):
    """Coordination DB identity metadata does not match the certified authority.

    Covers: missing identity metadata (legacy/0.4.0 DB), account_key
    mismatch, db_uuid mismatch (recreated DB at the same path), authority_id
    mismatch, and missing certified DB file. Always fail closed.
    """
