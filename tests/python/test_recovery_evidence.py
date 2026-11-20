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
