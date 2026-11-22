"""Deterministic, offline experiments for bounded failure attribution."""
import json

import pytest

from domain.failure_attribution import analyze_failure_attribution

PLAN_ID = "plan-1"
RUN_ID = "run-1"
RUN = {"plan_id": PLAN_ID, "run_id": RUN_ID, "branch_version": 1, "status": "Failed"}


def step(step_id, dependencies=(), order=1):
    return {
        "step_id": step_id, "plan_id": PLAN_ID, "branch_version": 1,
        "is_obsolete": 0, "step_order": order, "tool": "coding_agent",
        "depends_on": list(dependencies),
    }


def attempt(
    step_id, status="Failed", outputs=None, run_id=RUN_ID,
    plan_id=PLAN_ID, attempt_no=1,
    started_at="2026-01-01T00:00:00Z",
    finished_at="2026-01-01T00:00:01Z",
):
    return {
        "step_id": step_id, "run_id": run_id, "plan_id": plan_id,
        "attempt_no": attempt_no, "status": status,
        "outputs": outputs or {"success": False, "check_status": "unverified"},
        "started_at": started_at, "finished_at": finished_at,
    }


def analyze(steps, attempts, *, run=RUN, events=None):
    return analyze_failure_attribution(
        run=run, steps=steps, attempts=attempts, events=events,
    )


def test_chain_selects_earliest_failed_ancestor_without_claiming_causality():
    steps = [step("a", order=1), step("b", ["a"], 2), step("c", ["b"], 3)]
    attempts = [
        attempt("a"),
        attempt("b", started_at="2026-01-01T00:00:02Z"),
        attempt("c", started_at="2026-01-01T00:00:04Z"),
    ]
    result = analyze(steps, attempts)
    assert result["attribution"]["status"] == "candidate"
    assert result["attribution"]["candidate_step_ids"] == ["a"]
    assert result["attribution"]["root_cause_verified"] is False
    assert "upstream_dependency_and_time_order_observed" in result["attribution"]["basis"]


def test_independent_failures_are_ambiguous():
    result = analyze([step("a"), step("b", order=2)], [attempt("a"), attempt("b")])
    assert result["attribution"]["status"] == "ambiguous"
    assert result["attribution"]["candidate_step_ids"] == ["a", "b"]


def test_foreign_run_attempts_are_excluded():
    result = analyze([step("a")], [attempt("a", run_id="run-2")])
    assert result["attribution"]["status"] == "insufficient_evidence"
    assert result["provenance"]["excluded_attempts"] == {"different_run": 1}


def test_attempts_from_other_plan_are_excluded():
    result = analyze([step("a")], [attempt("a", plan_id="plan-2")])
    assert result["attribution"]["status"] == "insufficient_evidence"
    assert result["provenance"]["excluded_attempts"] == {"different_or_missing_plan": 1}


def test_unscoped_events_are_never_used_as_run_evidence():
    result = analyze(
        [step("a")], [],
        events=[{"event_type": "failed", "step_id": "a"}],
    )
    assert result["provenance"]["unscoped_event_count"] == 1
    assert result["provenance"]["events_used_for_attribution"] is False
    assert result["attribution"]["status"] == "insufficient_evidence"


def test_status_output_conflict_is_direct_invariant_violation():
    result = analyze([step("a")], [attempt("a", "Succeeded", {"success": False})])
    assert result["attribution"]["candidate_step_ids"] == ["a"]
    assert result["invariant_violations"][0]["code"] == "attempt_status_output_conflict"


def test_acceptance_status_conflict_is_detected():
    output = {
        "success": True, "check_status": "passed",
        "acceptance": {
            "passed": False,
            "checks": [{
                "type": "test_passes", "passed": False,
                "evidence": {"exit_code": 1},
            }],
        },
    }
    result = analyze([step("a")], [attempt("a", "Succeeded", output)])
    assert result["invariant_violations"][0]["code"] == "acceptance_status_conflict"


def test_dependency_must_finish_before_dependent_starts():
    steps = [step("a", order=1), step("b", ["a"], 2)]
    attempts = [
        attempt("a", started_at="2026-01-01T00:00:00Z",
                finished_at="2026-01-01T00:00:01Z"),
        attempt("b", started_at="2026-01-01T00:00:02Z",
                finished_at="2026-01-01T00:00:03Z"),
    ]
    result = analyze(steps, attempts)
    assert any(
        item["code"] == "dependent_started_before_dependency_success"
        for item in result["invariant_violations"]
    )
    assert result["attribution"]["candidate_step_ids"] == ["a"]


def test_successful_dependency_before_dependent_start_is_not_flagged():
    steps = [step("a", order=1), step("b", ["a"], 2)]
    attempts = [
        attempt("a", "Succeeded", {"success": True, "check_status": "passed"},
                started_at="2026-01-01T00:00:00Z",
                finished_at="2026-01-01T00:00:01Z"),
        attempt("b", "Failed", started_at="2026-01-01T00:00:02Z",
                finished_at="2026-01-01T00:00:03Z"),
    ]
    result = analyze(steps, attempts)
    assert not any(
        item["code"] == "dependent_started_before_dependency_success"
        for item in result["invariant_violations"]
    )
    assert result["attribution"]["candidate_step_ids"] == ["b"]


def test_legacy_no_dependency_graph_does_not_infer_order():
    result = analyze(
        [step("a", order=1), step("b", order=2)],
        [attempt("a"), attempt("b")],
    )
    assert result["graph"]["mode"] == "no_explicit_dependencies"
    assert result["attribution"]["status"] == "ambiguous"


def test_missing_dependency_rejects_attribution():
    result = analyze([step("a", ["missing"])], [attempt("a")])
    assert result["attribution"]["status"] == "invalid_trace"
    assert result["graph"]["error"] == "missing_dependency"


def test_cycle_rejects_attribution():
    result = analyze(
        [step("a", ["b"]), step("b", ["a"], 2)],
        [attempt("a")],
    )
    assert result["attribution"]["status"] == "invalid_trace"
    assert result["graph"]["error"] == "invalid_dependency_graph"


def test_secrets_and_raw_outputs_are_not_projected():
    secret = "DO_NOT_LEAK_THIS"
    output = {
        "success": False, "check_status": "failed",
        "error": secret, "stdout": secret,
        "acceptance": {
            "passed": False,
            "checks": [{
                "type": "test_passes", "passed": False,
                "evidence": {"exit_code": 1, "path": secret, "stdout_tail": secret},
            }],
        },
    }
    result = analyze([step("a")], [attempt("a", outputs=output)])
    assert secret not in json.dumps(result)
    assert result["observations"][0]["latest_failed_check_types"] == ["test_passes"]


def test_latest_successful_attempt_clears_step_as_current_failure():
    attempts = [
        attempt("a", "Failed", attempt_no=1),
        attempt("a", "Succeeded", {"success": True, "check_status": "passed"},
                attempt_no=2, started_at="2026-01-01T00:00:02Z",
                finished_at="2026-01-01T00:00:03Z"),
        attempt("b", "Failed"),
    ]
    result = analyze([step("a"), step("b", order=2)], attempts)
    assert result["attribution"]["candidate_step_ids"] == ["b"]


def test_run_record_must_be_failed():
    result = analyze(
        [step("a")], [attempt("a")],
        run={**RUN, "status": "Completed"},
    )
    assert result["attribution"]["status"] == "invalid_trace"
    assert result["graph"]["error"] == "run_not_failed"


def test_parent_step_alias_resolves_to_active_clone():
    clone = step("clone", order=1)
    clone["parent_step_id"] = "old-step"
    downstream = step("downstream", ["old-step"], 2)
    result = analyze(
        [clone, downstream],
        [attempt("clone"), attempt("downstream", started_at="2026-01-01T00:00:02Z")],
    )
    assert result["graph"]["edges"] == [{"from": "clone", "to": "downstream"}]
    assert result["attribution"]["candidate_step_ids"] == ["clone"]


def test_malformed_enum_values_do_not_crash_or_leak():
    output = {
        "success": False, "check_status": ["failed"],
        "blocked_reason": {"unexpected": "value"},
        "acceptance": {
            "passed": False,
            "checks": [{"type": ["untrusted"], "passed": False}],
        },
    }
    result = analyze([step("a")], [attempt("a", outputs=output)])
    assert result["observations"][0]["latest_failure_category"] == "acceptance_failure"
    assert result["observations"][0]["latest_failed_check_types"] == ["unknown"]


def test_attempt_history_truncation_refuses_unique_attribution():
    attempts = [attempt("a", attempt_no=index) for index in range(1, 1002)]
    result = analyze([step("a")], attempts)
    assert result["provenance"]["attempts_truncated"] is True
    assert result["attribution"]["status"] == "insufficient_evidence"
    assert result["attribution"]["candidate_step_ids"] == []


def test_active_branch_filter_excludes_obsolete_and_other_branch_steps():
    active = step("active")
    obsolete = step("obsolete", order=2)
    obsolete["is_obsolete"] = 1
    other_branch = step("other-branch", order=3)
    other_branch["branch_version"] = 2
    result = analyze(
        [active, obsolete, other_branch],
        [attempt("active"), attempt("obsolete"), attempt("other-branch")],
    )
    assert [item["step_id"] for item in result["observations"]] == ["active"]
    assert result["provenance"]["excluded_attempts"] == {
        "step_not_in_active_branch": 2,
    }


def test_oversized_serialized_output_is_not_parsed():
    result = analyze(
        [step("a")],
        [attempt("a", outputs='{"success": false}' + (" " * 131073))],
    )
    assert result["observations"][0]["latest_failure_category"] == "unknown"
    assert result["attribution"]["status"] == "candidate"


def test_upstream_failure_after_downstream_started_does_not_win_attribution():
    steps = [step("a", order=1), step("b", ["a"], 2)]
    attempts = [
        attempt("a", started_at="2026-01-01T00:00:05Z",
                finished_at="2026-01-01T00:00:06Z"),
        attempt("b", started_at="2026-01-01T00:00:02Z",
                finished_at="2026-01-01T00:00:03Z"),
    ]
    result = analyze(steps, attempts)
    assert result["attribution"]["status"] == "ambiguous"
    assert result["attribution"]["candidate_step_ids"] == ["a", "b"]


def test_missing_timestamps_do_not_create_dependency_causality():
    steps = [step("a", order=1), step("b", ["a"], 2)]
    attempts = [
        attempt("a", started_at=None, finished_at=None),
        attempt("b", started_at=None, finished_at=None),
    ]
    result = analyze(steps, attempts)
    assert result["attribution"]["status"] == "ambiguous"
    assert result["attribution"]["candidate_step_ids"] == ["a", "b"]


def test_historical_invariant_violation_does_not_override_latest_success():
    attempts = [
        attempt("a", "Succeeded", {"success": False}, attempt_no=1),
        attempt("a", "Succeeded", {"success": True, "check_status": "passed"},
                attempt_no=2, started_at="2026-01-01T00:00:02Z",
                finished_at="2026-01-01T00:00:03Z"),
        attempt("b", "Failed"),
    ]
    result = analyze([step("a"), step("b", order=2)], attempts)
    assert any(
        item["step_id"] == "a" and item["code"] == "attempt_status_output_conflict"
        for item in result["invariant_violations"]
    )
    assert result["attribution"]["candidate_step_ids"] == ["b"]


def test_inactive_history_does_not_consume_active_step_budget():
    rows = [step("active")]
    for index in range(300):
        old = step(f"old-{index}", order=index + 2)
        old["is_obsolete"] = 1
        rows.append(old)
    result = analyze(rows, [attempt("active")])
    assert result["graph"]["valid"] is True
    assert result["graph"]["step_count"] == 1
    assert result["attribution"]["candidate_step_ids"] == ["active"]
