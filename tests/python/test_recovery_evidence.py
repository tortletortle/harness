"""Controlled evidence-projection experiments, no external models or tools."""
import json

import pytest

from domain.recovery_evidence import summarize_failure


@pytest.mark.parametrize("serialized", [False, True])
def test_same_generic_error_different_observed_symptoms(serialized):
    observations = [
        {"success": False, "check_status": "failed", "acceptance_result": {"passed": False,
         "checks": [{"type": "test_passes", "passed": False, "evidence": {"exit_code": 1}}]}},
        {"success": False, "check_status": "unverified", "blocked": True,
         "blocked_reason": "resource_denied", "manual_retry_required": True},
        {"success": False, "check_status": "unverified", "blocked": False,
         "retry_policy": {"eligible": True}},
    ]
    rows = [{"failure_reason": "failed", "state_snapshot": json.dumps(item) if serialized else item}
            for item in observations]
    summaries = [summarize_failure(row) for row in rows]
    assert [item["symptom"] for item in summaries] == [
        "acceptance_failure", "blocked", "execution_failure"
    ]
    assert summaries[0]["failed_checks"] == [{"index": 0, "type": "test_passes", "exit_code": 1}]
    assert summaries[1]["blocked_reason"] == "resource_denied"
    assert summaries[2]["retry_eligible"] is True
    assert all(item["reproducibility"] == "unknown" and not item["root_cause_verified"]
               for item in summaries)


@pytest.mark.parametrize("invalid", [None, "{bad", "[]" , "x" * 131073])
def test_missing_or_invalid_snapshot_uses_legacy_outputs(invalid):
    summary = summarize_failure({"state_snapshot": invalid,
                                 "outputs": '{"check_status":"failed"}'})
    assert summary["source"] == "outputs"
    assert summary["symptom"] == "acceptance_failure"
    assert summary["retry_eligible"] is None


def test_empty_evidence_is_not_a_diagnosis():
    summary = summarize_failure({})
    assert summary["source"] == "missing"
    assert summary["symptom"] == "unknown"
    assert not summary["root_cause_verified"]


def test_allowlist_and_caps_exclude_opaque_strings():
    secret = "DO_NOT_COPY_SECRET"
    check = {"type": secret, "passed": False, "evidence": {
        "stdout_tail": secret, "path": secret, "exit_code": secret, "exists": False}}
    snapshot = {"check_status": secret, "blocked_reason": secret, "error": secret,
                "retry_policy": {"eligible": secret}, "acceptance_result": {"checks": [check] * 30}}
    summary = summarize_failure({"state_snapshot": snapshot})
    assert secret not in json.dumps(summary)
    assert len(summary["failed_checks"]) == 16
    assert summary["checks_truncated"]
    assert summary["failed_checks"][0] == {"index": 0, "type": "unknown", "exists": False}
    assert summary["retry_eligible"] is None


def test_snapshot_and_outputs_are_not_merged():
    summary = summarize_failure({"state_snapshot": {"success": False},
                                 "outputs": {"check_status": "failed", "retry_policy": {"eligible": True}}})
    assert summary["symptom"] == "execution_failure"
    assert summary["check_status"] is None
    assert summary["retry_eligible"] is None


def _failed_result(code=1):
    return {"success": False, "check_status": "failed",
            "acceptance_result": {"passed": False, "checks": [
                {"type": "test_passes", "passed": False, "evidence": {"exit_code": code}}
            ]}}


@pytest.mark.parametrize("codes, pattern, matches, different", [
    ([], "insufficient_evidence", 0, 0),
    ([1], "single_matching_observation", 1, 0),
    ([1, 1], "repeated_signature_observed", 2, 0),
    ([2, 2], "no_matching_observation", 0, 2),
    ([1, 2, 1], "repeated_signature_observed", 2, 1),
])
def test_recurrence_counts_only_matching_signatures(codes, pattern, matches, different):
    from domain.recovery_evidence import summarize_attempt_history
    anchor = {"state_snapshot": _failed_result()}
    attempts = [{"status": "Failed", "outputs": json.dumps(_failed_result(code))} for code in codes]
    history = summarize_attempt_history(anchor, attempts)
    assert history["pattern"] == pattern
    assert history["matching_failures"] == matches
    assert history["different_failures"] == different
    assert history["controlled_conditions"] is False
    assert history["reproducibility"] == "unknown"
    assert not history["root_cause_verified"]


def test_generic_failure_is_not_a_repeated_signature():
    from domain.recovery_evidence import summarize_attempt_history
    result = {"success": False, "error": "same error"}
    history = summarize_attempt_history({"state_snapshot": result},
        [{"status": "Failed", "outputs": result}] * 3)
    assert history["pattern"] == "insufficient_evidence"
    assert history["matching_failures"] == 0
    assert history["unclassified"] == 3


def test_history_window_and_attempt_status_are_conservative():
    from domain.recovery_evidence import summarize_attempt_history
    failed = _failed_result()
    rows = [
        {"status": "Succeeded", "outputs": {"success": True, "check_status": "passed"}},
        {"status": "Succeeded", "outputs": {"success": True, "check_status": "unverified"}},
        {"status": "Running", "outputs": failed},
    ] + [{"status": "Failed", "outputs": failed}] * 20
    history = summarize_attempt_history({"state_snapshot": failed}, rows)
    assert history["recorded_attempts"] == 16
    assert history["matching_failures"] == 13
    assert history["reported_checked_successes"] == 1
    assert history["unclassified"] == 2
    assert history["truncated"] is True


def _fingerprinted_failure(workspace_hash, after_hash=None, complete=True):
    result = _failed_result()
    before = {"version": 1, "complete": complete,
              "workspace_sha256": workspace_hash if complete else None}
    after = {"version": 1, "complete": complete,
             "workspace_sha256": (after_hash or workspace_hash) if complete else None}
    result["_recovery_context"] = {
        "workspace_before": before,
        "workspace_after": after,
    }
    return result


def test_matching_failures_with_same_workspace_fingerprint_are_only_observational():
    from domain.recovery_evidence import summarize_attempt_history
    anchor = {"state_snapshot": _failed_result()}
    rows = [{"status": "Failed", "outputs": _fingerprinted_failure("a" * 64)} for _ in range(2)]
    history = summarize_attempt_history(anchor, rows)
    assert history["pattern"] == "repeated_signature_observed"
    assert history["precondition_fingerprint_consistency"] == "same"
    assert history["matching_failures_same_precondition"] is True
    assert history["controlled_conditions"] is False
    assert history["reproducibility"] == "unknown"


def test_same_failure_signature_with_different_fingerprints_is_not_same_precondition():
    from domain.recovery_evidence import summarize_attempt_history
    anchor = {"state_snapshot": _failed_result()}
    rows = [
        {"status": "Failed", "outputs": _fingerprinted_failure("a" * 64)},
        {"status": "Failed", "outputs": _fingerprinted_failure("b" * 64)},
    ]
    history = summarize_attempt_history(anchor, rows)
    assert history["pattern"] == "repeated_signature_observed"
    assert history["precondition_fingerprint_consistency"] == "varied"
    assert history["matching_failures_same_precondition"] is False


def test_incomplete_fingerprint_never_counts_as_same_precondition():
    from domain.recovery_evidence import summarize_attempt_history
    anchor = {"state_snapshot": _failed_result()}
    rows = [
        {"status": "Failed", "outputs": _fingerprinted_failure("a" * 64)},
        {"status": "Failed", "outputs": _fingerprinted_failure(None, complete=False)},
    ]
    history = summarize_attempt_history(anchor, rows)
    assert history["precondition_fingerprint_consistency"] == "insufficient"
    assert history["matching_failures_same_precondition"] is False
    assert history["incomplete_fingerprint_attempts"] == 1


def test_workspace_mutation_during_attempt_is_reported():
    from domain.recovery_evidence import summarize_attempt_history
    anchor = {"state_snapshot": _failed_result()}
    rows = [{"status": "Failed",
             "outputs": _fingerprinted_failure("a" * 64, after_hash="b" * 64)}]
    history = summarize_attempt_history(anchor, rows)
    assert history["workspace_changed_attempts"] == 1
