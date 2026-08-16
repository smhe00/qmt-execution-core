from __future__ import annotations

from qmt_execution_core.formal import (
    verify_runtime_transition_refinement,
    verify_three_process_coordination,
)


def test_runtime_transition_refinement_matches_executable_state_machine() -> None:
    report = verify_runtime_transition_refinement()
    assert report["illegal_observation_edges"] == 0
    assert report["undeclared_runtime_events"] == 0
    assert report["declared_events_without_runtime_emitter"] == 0
    assert report["hidden_runtime_state_mutations"] == 0
    assert report["dynamic_transition_call_sites"] == 1


def test_three_independent_process_product_state_space() -> None:
    report = verify_three_process_coordination()
    assert report["processes"] == 3
    assert report["scenario_count"] == 4
    assert report["same_symbol_exclusivity_violations"] == 0
    assert report["shared_cash_authorization_violations"] == 0
    assert report["resource_release_violations"] == 0
    assert report["quarantine_claim_violations"] == 0
    assert report["cross_symbol_concurrency_witness"] is True
    assert report["cross_account_same_symbol_concurrency_witness"] is True
    assert report["total_reachable_global_states"] > 0
    assert report["total_reachable_interleaving_edges"] > 0
