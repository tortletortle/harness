"""Controlled diagnostic protocol tests: design only, no tool execution."""
from domain.recovery_experiments import build_diagnostic_protocol


def _experiments(protocol):
    assert protocol["mode"] == "design_only"
    assert protocol["execution_authorized"] is False
    assert protocol["automatic_retry_authorized"] is False
    assert protocol["root_cause_verified"] is False
    assert protocol["reproducibility"] == "unknown"
    assert all(item["execution_mode"] == "proposal_only"
               and item["execution_authorized"] is False
               and item["may_invoke_tools"] is False
               and item["readiness"] == "design_only_no_runner"
               and "experiment_runner_not_implemented" in item["execution_blockers"]
               for item in protocol["experiments"])
    return protocol["experiments"]


def test_blocked_failure_never_proposes_replay():
    protocol = build_diagnostic_protocol(
        {"symptom": "blocked", "blocked_reason": "resource_denied",
         "history": {"pattern": "repeated_signature_observed"}},
        {"side_effect": "external", "idempotent": False},
    )
    experiments = _experiments(protocol)
    assert [item["id"] for item in experiments] == ["inspect_saved_block_decision"]
    assert protocol["source_tool_policy"]["future_sandbox_candidate"] is False
    assert "authorization_or_policy_boundary" in [h["id"] for h in protocol["hypotheses"]]


def test_test_acceptance_proposes_one_variable_frozen_snapshot_replay():
    protocol = build_diagnostic_protocol({
        "symptom": "acceptance_failure",
        "failed_checks": [{"type": "test_passes"}, {"type": "exit_code"}],
        "history": {"pattern": "repeated_signature_observed"},
    })
    experiments = _experiments(protocol)
    replay = next(item for item in experiments
                  if item["id"] == "repeat_acceptance_on_frozen_snapshot")
    assert replay["variable_under_test"] == "acceptance_invocation"
    assert "source_revision" in replay["controlled_variables"]
    assert "workspace_snapshot_id" in replay["controlled_variables"]
    assert "disposable_isolated_worktree" in replay["required_prerequisites"]
    assert "stable_failure_signature" in [h["id"] for h in protocol["hypotheses"]]


def test_artifact_acceptance_does_not_propose_test_execution():
    protocol = build_diagnostic_protocol({
        "symptom": "acceptance_failure",
        "failed_checks": [{"type": "file_exists"}],
    })
    experiments = _experiments(protocol)
    assert [item["id"] for item in experiments] == ["inspect_saved_acceptance_contract"]


def test_execution_failure_uses_observation_not_automatic_retry():
    protocol = build_diagnostic_protocol({"symptom": "execution_failure"})
    experiments = _experiments(protocol)
    assert [item["id"] for item in experiments] == ["inspect_saved_attempt_metadata"]


def test_needs_input_waits_for_user_instead_of_guessing():
    protocol = build_diagnostic_protocol({"symptom": "needs_input"})
    experiments = _experiments(protocol)
    assert [item["id"] for item in experiments] == ["review_missing_input_boundary"]


def test_unknown_failure_only_collects_minimal_evidence():
    protocol = build_diagnostic_protocol({"symptom": "unknown"})
    experiments = _experiments(protocol)
    assert [item["id"] for item in experiments] == ["collect_minimal_failure_observations"]


def test_unknown_tool_metadata_fails_closed():
    protocol = build_diagnostic_protocol(
        {"symptom": "execution_failure"},
        {"side_effect": "surprise", "idempotent": True},
    )
    assert protocol["source_tool_policy"] == {
        "side_effect": "unknown", "idempotent": None, "future_sandbox_candidate": False,
        "replay_authorized": False,
    }


def test_only_explicit_read_only_idempotent_capability_is_replay_candidate():
    safe = build_diagnostic_protocol(
        {"symptom": "execution_failure"},
        {"side_effect": "none", "idempotent": True},
    )
    unsafe = build_diagnostic_protocol(
        {"symptom": "execution_failure"},
        {"side_effect": "reversible", "idempotent": True},
    )
    assert safe["source_tool_policy"]["future_sandbox_candidate"] is True
    assert safe["source_tool_policy"]["replay_authorized"] is False
    assert unsafe["source_tool_policy"]["future_sandbox_candidate"] is False


def test_protocol_does_not_copy_untrusted_evidence_strings():
    marker = "DO_NOT_COPY_SECRET_OR_PROMPT"
    protocol = build_diagnostic_protocol({
        "symptom": "acceptance_failure",
        "failed_checks": [{"type": marker, "raw_log": marker}],
        "history": {"pattern": marker},
        "error": marker,
    })
    serialized = str(protocol)
    assert marker not in serialized
    _experiments(protocol)


def test_same_observed_fingerprint_adds_hypothesis_without_claiming_causality():
    protocol = build_diagnostic_protocol({
        "symptom": "acceptance_failure",
        "history": {
            "pattern": "repeated_signature_observed",
            "precondition_fingerprint_consistency": "same",
            "matching_failures_same_precondition": True,
        },
    })
    ids = [item["id"] for item in protocol["hypotheses"]]
    assert "matching_failure_under_same_observed_fingerprint" in ids
    assert protocol["attempt_context_consistency"] == "same"
    assert protocol["root_cause_verified"] is False
    assert protocol["reproducibility"] == "unknown"


def test_varied_fingerprints_add_environment_variation_hypothesis():
    protocol = build_diagnostic_protocol({
        "symptom": "execution_failure",
        "history": {
            "pattern": "repeated_signature_observed",
            "precondition_fingerprint_consistency": "varied",
        },
    })
    ids = [item["id"] for item in protocol["hypotheses"]]
    assert "execution_context_varied_between_attempts" in ids
    assert protocol["attempt_context_consistency"] == "varied"
    assert protocol["controlled_conditions"] if "controlled_conditions" in protocol else True
    assert protocol["root_cause_verified"] is False
