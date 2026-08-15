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
