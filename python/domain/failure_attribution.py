"""Deterministic, bounded failure attribution over one execution Run.

This is an offline analysis primitive, not a retry policy or a causal oracle.
Only run-scoped execution_attempts and the active branch's explicit WorkGraph
are used. step_events are deliberately excluded because their schema has no
run_id and therefore cannot be assigned to one execution Run safely.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from domain.work_graph import normalize_dependencies, validate_graph

SCHEMA_VERSION = 1
MAX_TRACE_STEPS = 256
MAX_INPUT_STEPS = 2048
MAX_TRACE_ATTEMPTS = 1000
MAX_OUTPUT_CHARS = 131072
MAX_CHECKS = 16
_ALLOWED_CHECK_TYPES = frozenset({
    "file_exists", "content_contains", "exit_code", "diff_scope", "test_passes",
})
_ALLOWED_CHECK_STATUS = frozenset({"passed", "failed", "unverified"})
_ALLOWED_BLOCK_REASONS = frozenset({
    "tool_unregistered", "grant_not_found", "grant_revoked", "grant_expired",
    "tool_not_granted", "resource_denied", "budget_exceeded", "grant_required",
    "user_rejected", "approval_timeout", "invalid_desktop_scope",
    "desktop_scope_changed",
})


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and len(value) <= MAX_OUTPUT_CHARS:
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError, RecursionError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _int(value: Any, default: int = 0) -> int:
    if type(value) is int:
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
    return default


def _time(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _is_obsolete(row: Mapping[str, Any]) -> bool:
    value = row.get("is_obsolete", False)
    return value is True or value == 1 or value == "1"


def _project_output(value: Any) -> dict[str, Any]:
    """Keep only bounded, allowlisted fields; never return raw model/tool text."""
    raw = _object(value)
    acceptance = _object(raw.get("acceptance_result") or raw.get("acceptance"))
    raw_checks = acceptance.get("checks")
    checks = raw_checks if isinstance(raw_checks, list) else []
    failed_checks = []
    for index, item in enumerate(checks[:MAX_CHECKS]):
        if not isinstance(item, dict) or item.get("passed") is not False:
            continue
        check_type = item.get("type")
        record: dict[str, Any] = {
            "index": index,
            "type": check_type if isinstance(check_type, str) and check_type in _ALLOWED_CHECK_TYPES else "unknown",
        }
        evidence = _object(item.get("evidence"))
        for key in ("exists", "is_file", "is_directory"):
            if isinstance(evidence.get(key), bool):
                record[key] = evidence[key]
        code = evidence.get("exit_code")
        if type(code) is int and -(2**31) <= code < 2**31:
            record["exit_code"] = code
        failed_checks.append(record)

    check_status = raw.get("check_status")
    if not isinstance(check_status, str) or check_status not in _ALLOWED_CHECK_STATUS:
        check_status = None
    blocked = _bool(raw.get("blocked"))
    blocked_reason = raw.get("blocked_reason")
    if not isinstance(blocked_reason, str) or blocked_reason not in _ALLOWED_BLOCK_REASONS:
        blocked_reason = None
    success = _bool(raw.get("success"))
    acceptance_passed = _bool(acceptance.get("passed"))

    if blocked is True:
        category = "blocked"
    elif check_status == "failed" or acceptance_passed is False or failed_checks:
        category = "acceptance_failure"
    elif success is False:
        category = "execution_failure"
    else:
        category = "unknown"

    return {
        "success": success,
        "check_status": check_status,
        "blocked": blocked,
        "blocked_reason": blocked_reason,
        "acceptance_passed": acceptance_passed,
        "failed_checks": failed_checks,
        "checks_truncated": len(checks) > MAX_CHECKS,
        "category": category,
        "output_missing_or_invalid": not bool(raw),
        "output_too_large": isinstance(value, str) and len(value) > MAX_OUTPUT_CHARS,
    }


def _invalid_result(plan_id: str, run_id: str, branch_version: int, reason: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": {"plan_id": plan_id, "run_id": run_id, "branch_version": branch_version},
        "graph": {"valid": False, "mode": "unavailable", "step_count": 0, "edges": [], "error": reason},
        "observations": [],
        "invariant_violations": [],
        "attribution": {
            "status": "invalid_trace", "candidate_step_ids": [],
            "primary_candidate_step_id": None, "basis": [reason],
            "root_cause_verified": False,
        },
        "provenance": {
            "attempts_run_scoped": False, "accepted_attempt_count": 0,
            "excluded_attempts": {}, "attempts_truncated": False,
            "unscoped_event_count": 0, "events_used_for_attribution": False,
            "controlled_conditions": False,
        },
    }


def analyze_failure_attribution(
    *,
    run: Mapping[str, Any],
    steps: Sequence[Mapping[str, Any]],
    attempts: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a safe Trace IR and rank earliest observed failed ancestors.

    Caller must pass the authoritative execution_runs row and obtain
    attempts from that row's run_id. The function rechecks run_id and plan_id
    on every attempt and filters steps to the Run's branch_version. It never
    infers dependencies from step_order for legacy linear plans, and it never
    treats a dependency path as proof of causation.
    """
    if not isinstance(run, Mapping):
        return _invalid_result("", "", -1, "missing_run_record")
    plan_id = str(run.get("plan_id") or "")
    run_id = str(run.get("run_id") or "")
    branch_version = _int(run.get("branch_version"), -1)
    if not plan_id or not run_id or branch_version < 1:
        return _invalid_result(plan_id, run_id, branch_version, "invalid_run_scope")
    if str(run.get("status") or "").lower() != "failed":
        return _invalid_result(plan_id, run_id, branch_version, "run_not_failed")
    if not isinstance(steps, (list, tuple)) or len(steps) > MAX_INPUT_STEPS:
        return _invalid_result(plan_id, run_id, branch_version, "step_input_limit_exceeded_or_invalid")

    active_rows: list[Mapping[str, Any]] = []
    for row in steps:
        if not isinstance(row, Mapping):
            return _invalid_result(plan_id, run_id, branch_version, "invalid_step_row")
        row_plan = row.get("plan_id")
        if row_plan is not None and str(row_plan) != plan_id:
            return _invalid_result(plan_id, run_id, branch_version, "step_plan_mismatch")
        row_branch = _int(row.get("branch_version", 1), 1)
        if row_branch != branch_version or _is_obsolete(row):
            continue
        active_rows.append(row)
    if len(active_rows) > MAX_TRACE_STEPS:
        return _invalid_result(plan_id, run_id, branch_version, "active_step_limit_exceeded")

    step_rows: dict[str, Mapping[str, Any]] = {}
    order_key: dict[str, tuple[int, str]] = {}
    for row in active_rows:
        step_id = str(row.get("step_id") or "").strip()
        if not step_id or len(step_id) > 256 or step_id in step_rows:
            return _invalid_result(plan_id, run_id, branch_version, "missing_or_duplicate_step_id")
        step_rows[step_id] = row
        order_key[step_id] = (_int(row.get("step_order"), 0), step_id)
    if not step_rows:
        return _invalid_result(plan_id, run_id, branch_version, "no_active_steps")

    aliases: dict[str, str] = {}
    ambiguous_aliases: set[str] = set()
    for step_id, row in step_rows.items():
        parent = row.get("parent_step_id")
        if parent:
            parent_id = str(parent)
            if parent_id in aliases and aliases[parent_id] != step_id:
                ambiguous_aliases.add(parent_id)
            aliases[parent_id] = step_id
    if ambiguous_aliases:
        return _invalid_result(plan_id, run_id, branch_version, "ambiguous_parent_step_alias")

    dependencies: dict[str, list[str]] = {}
    edges: list[dict[str, str]] = []
    try:
        for step_id, row in step_rows.items():
            raw_deps = normalize_dependencies(row.get("depends_on"))
            resolved = [aliases.get(dep, dep) for dep in raw_deps]
            missing = [dep for dep in resolved if dep not in step_rows]
            if missing:
                return _invalid_result(plan_id, run_id, branch_version, "missing_dependency")
            dependencies[step_id] = resolved
            edges.extend({"from": dep, "to": step_id} for dep in resolved)
    except (TypeError, ValueError):
        return _invalid_result(plan_id, run_id, branch_version, "invalid_dependency_shape")

    graph_check = validate_graph([
        {"step_id": step_id, "depends_on": dependencies[step_id]}
        for step_id in step_rows
    ])
    if not graph_check.valid:
        return _invalid_result(plan_id, run_id, branch_version, "invalid_dependency_graph")

    ancestors: dict[str, set[str]] = {}

    def collect_ancestors(step_id: str) -> set[str]:
        if step_id in ancestors:
            return ancestors[step_id]
        found: set[str] = set()
        for dependency in dependencies[step_id]:
            found.add(dependency)
            found.update(collect_ancestors(dependency))
        ancestors[step_id] = found
        return found

    for step_id in step_rows:
        collect_ancestors(step_id)

    supplied_attempts = list(attempts) if isinstance(attempts, (list, tuple)) else []
    attempts_truncated = len(supplied_attempts) > MAX_TRACE_ATTEMPTS
    supplied_attempts = supplied_attempts[:MAX_TRACE_ATTEMPTS]
    excluded = defaultdict(int)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in supplied_attempts:
        if not isinstance(row, Mapping):
            excluded["invalid_row"] += 1
            continue
        if not row.get("run_id"):
            excluded["missing_run_id"] += 1
            continue
        if str(row.get("run_id")) != run_id:
            excluded["different_run"] += 1
            continue
        if not row.get("plan_id") or str(row.get("plan_id")) != plan_id:
            excluded["different_or_missing_plan"] += 1
            continue
        step_id = str(row.get("step_id") or "")
        if step_id not in step_rows:
            excluded["step_not_in_active_branch"] += 1
            continue

        projected = _project_output(row.get("outputs"))
        status = str(row.get("status") or "unknown")[:32]
        item = {
            "step_id": step_id,
            "attempt_no": _int(row.get("attempt_no"), 0),
            "status": status,
            "status_key": status.lower(),
            "started_ts": _time(row.get("started_at")),
            "finished_ts": _time(row.get("finished_at")),
            "attempt_id": str(row.get("attempt_id") or "")[:128],
            "projection": projected,
        }
        grouped[step_id].append(item)

    for step_id in grouped:
        grouped[step_id].sort(key=lambda item: (
            item["attempt_no"],
            item["started_ts"] if item["started_ts"] is not None else float("inf"),
            item["attempt_id"],
        ))

    violations: list[dict[str, Any]] = []
    violation_nodes: set[str] = set()
    violation_times: dict[str, float] = {}
    for step_id, rows in grouped.items():
        for item in rows:
            p = item["projection"]
            code = None
            if ((item["status_key"] == "succeeded" and p["success"] is False)
                    or (item["status_key"] == "failed" and p["success"] is True)):
                code = "attempt_status_output_conflict"
            elif (p["check_status"] == "passed"
                  and (p["acceptance_passed"] is False or bool(p["failed_checks"]))):
                code = "acceptance_status_conflict"
            elif p["blocked_reason"] and p["blocked"] is False:
                code = "blocked_reason_without_blocked_flag"
            elif item["status_key"] == "succeeded" and p["category"] == "acceptance_failure":
                code = "successful_attempt_with_failed_acceptance"
            if code:
                violations.append({
                    "code": code, "step_id": step_id,
                    "attempt_no": item["attempt_no"],
                })
                # Historical violations remain visible, but only a violation
                # on the latest attempt can influence current-run attribution.
                if item is rows[-1]:
                    violation_nodes.add(step_id)
                    observed_at = item["finished_ts"] if item["finished_ts"] is not None else item["started_ts"]
                    if observed_at is not None:
                        violation_times[step_id] = max(violation_times.get(step_id, float("-inf")), observed_at)
                observed_at = item["finished_ts"] if item["finished_ts"] is not None else item["started_ts"]
                if observed_at is not None:
                    violation_times[step_id] = max(violation_times.get(step_id, float("-inf")), observed_at)

    # A dependency attempted in this Run must have a successful attempt
    # completed before the dependent attempt starts. No same-Run dependency
    # attempt may mean prior-Run success legitimately satisfies the dependency.
    for dependent_id, dependent_rows in grouped.items():
        for dependent_attempt in dependent_rows:
            started = dependent_attempt["started_ts"]
            if started is None:
                continue
            for dependency_id in dependencies[dependent_id]:
                dependency_rows = grouped.get(dependency_id, [])
                if not dependency_rows:
                    continue
                completed_before = any(
                    row["status_key"] == "succeeded"
                    and row["finished_ts"] is not None
                    and row["finished_ts"] <= started
                    for row in dependency_rows
                )
                if not completed_before:
                    violations.append({
                        "code": "dependent_started_before_dependency_success",
                        "step_id": dependent_id,
                        "related_step_id": dependency_id,
                        "attempt_no": dependent_attempt["attempt_no"],
                    })
                    violation_nodes.add(dependent_id)
                    violation_times[dependent_id] = max(
                        violation_times.get(dependent_id, float("-inf")), started,
                    )

    observations = []
    failure_nodes: set[str] = set()
    latest_by_step: dict[str, dict[str, Any]] = {}
    for step_id in sorted(step_rows, key=lambda sid: order_key[sid]):
        rows = grouped.get(step_id, [])
        latest = rows[-1] if rows else None
        if latest is not None:
            latest_by_step[step_id] = latest
        failed_count = sum(1 for row in rows if row["status_key"] == "failed")
        if latest and latest["status_key"] == "failed":
            failure_nodes.add(step_id)
        observations.append({
            "step_id": step_id,
            "tool": str(step_rows[step_id].get("tool") or "")[:80],
            "attempt_count": len(rows),
            "failed_attempt_count": failed_count,
            "latest_attempt_status": latest["status"] if latest else None,
            "latest_failure_category": (
                latest["projection"]["category"]
                if latest and latest["status_key"] == "failed" else None
            ),
            "latest_failed_check_types": (
                list(dict.fromkeys(item["type"] for item in latest["projection"]["failed_checks"]))
                if latest and latest["status_key"] == "failed" else []
            ),
            "latest_checks_truncated": bool(latest and latest["projection"]["checks_truncated"]),
            "latest_output_too_large": bool(latest and latest["projection"]["output_too_large"]),
            "latest_output_missing_or_invalid": bool(latest and latest["projection"]["output_missing_or_invalid"]),
        })

    observed_nodes = failure_nodes | violation_nodes

    def source_observation_time(step_id: str) -> float | None:
        latest = latest_by_step.get(step_id)
        if latest and latest["status_key"] == "failed" and latest["finished_ts"] is not None:
            return latest["finished_ts"]
        return violation_times.get(step_id)

    def target_observation_time(step_id: str) -> float | None:
        latest = latest_by_step.get(step_id)
        if latest and latest["status_key"] == "failed" and latest["started_ts"] is not None:
            return latest["started_ts"]
        return violation_times.get(step_id)

    # A graph ancestor suppresses a downstream candidate only when its
    # observed failure/violation strictly predates the downstream observation.
    # Missing or equal-resolution timestamps do not establish temporal order.
    candidates = [
        step_id for step_id in observed_nodes
        if not any(
            ancestor in observed_nodes
            and source_observation_time(ancestor) is not None
            and target_observation_time(step_id) is not None
            and source_observation_time(ancestor) < target_observation_time(step_id)
            for ancestor in ancestors[step_id]
        )
    ]
    candidates.sort(key=lambda sid: order_key[sid])
    if attempts_truncated:
        attribution_status = "insufficient_evidence"
        basis = ["attempt_history_truncated"]
        candidates = []
    elif len(candidates) == 1:
        attribution_status = "candidate"
        basis = ["earliest_observed_failure_or_invariant_violation"]
        if any(
            candidates[0] in ancestors.get(other, set())
            and source_observation_time(candidates[0]) is not None
            and target_observation_time(other) is not None
            and source_observation_time(candidates[0]) < target_observation_time(other)
            for other in observed_nodes if other != candidates[0]
        ):
            basis.append("upstream_dependency_and_time_order_observed")
        else:
            basis.append("single_minimal_observed_node")
    elif len(candidates) > 1:
        attribution_status = "ambiguous"
        basis = ["multiple_independent_minimal_observed_nodes"]
    else:
        attribution_status = "insufficient_evidence"
        basis = ["no_run_scoped_failed_attempt_or_invariant_violation"]

    event_rows = list(events) if isinstance(events, (list, tuple)) else []
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": {"plan_id": plan_id, "run_id": run_id, "branch_version": branch_version},
        "graph": {
            "valid": True,
            "mode": "explicit_dependencies" if any(dependencies.values()) else "no_explicit_dependencies",
            "step_count": len(step_rows),
            "edges": edges,
        },
        "observations": observations,
        "invariant_violations": violations,
        "attribution": {
            "status": attribution_status,
            "candidate_step_ids": candidates,
            "primary_candidate_step_id": candidates[0] if len(candidates) == 1 else None,
            "basis": basis,
            "root_cause_verified": False,
        },
        "provenance": {
            "attempts_run_scoped": True,
            "accepted_attempt_count": sum(len(rows) for rows in grouped.values()),
            "excluded_attempts": dict(sorted(excluded.items())),
            "attempts_truncated": attempts_truncated,
            "unscoped_event_count": len(event_rows),
            "events_used_for_attribution": False,
            "event_exclusion_reason": "step_events have no run_id column",
            "controlled_conditions": False,
        },
    }
