import pytest

from qmt_execution_core.exceptions import RuntimeConfigurationError, RuntimeConfirmationError
from qmt_execution_core.miniqmt.runtime_gate import (
    RuntimeExecutionGate,
    RuntimeGateConfig,
    token_sha256,
)


def test_simulation_gate_is_ready_without_live_confirmation():
    gate = RuntimeExecutionGate(RuntimeGateConfig(environment="simulation"))
    assert gate.execution_allowed


def test_live_gate_requires_both_config_and_runtime_token():
    gate = RuntimeExecutionGate(
        RuntimeGateConfig(
            environment="live",
            live_trading_enabled=True,
            confirmation_token_sha256=token_sha256("secret"),
        )
    )
    assert not gate.execution_allowed
    with pytest.raises(RuntimeConfirmationError):
        gate.confirm("wrong")
    gate.confirm("secret")
    assert gate.execution_allowed
    gate.revoke()
    assert not gate.execution_allowed


def test_live_enabled_requires_token_digest():
    with pytest.raises(RuntimeConfigurationError):
        RuntimeGateConfig(environment="live", live_trading_enabled=True)
