"""Bounded observations for repair planning; never a retry authorization.

Only use the failed step from the consistent replan snapshot. No raw logs,
paths, model-generated prose, grant IDs, or other opaque output is copied.
The observations describe failure symptoms, not verified root causes.
"""
import json

_MAX_JSON_CHARS = 131072
_MAX_CHECKS = 16
_CHECK_TYPES = ("file_exists", "content_contains", "exit_code", "diff_scope", "test_passes")
_CHECK_STATUS = ("passed", "failed", "unverified")
_BLOCK_REASONS = (
    "tool_unregistered", "grant_not_found", "grant_revoked", "grant_expired",
    "tool_not_granted", "resource_denied", "budget_exceeded", "grant_required",
    "user_rejected", "approval_timeout",
)


def _object(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and len(value) <= _MAX_JSON_CHARS:
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError, RecursionError):
            pass
    return {}


def _enum(value, allowed):
    return value if isinstance(value, str) and value in allowed else None


def _bool(value):
    return value if isinstance(value, bool) else None


def summarize_failure(anchor):
    """Project the current failed row into a small, allowlisted record."""
    snapshot = _object(anchor.get("state_snapshot"))
    fallback = _object(anchor.get("outputs"))
    # Do not merge historical/partial sources into a synthetic attempt.
    data = snapshot or fallback
    source = "state_snapshot" if snapshot else ("outputs" if fallback else "missing")
    check_status = _enum(data.get("check_status"), _CHECK_STATUS)
    blocked = _bool(data.get("blocked"))
    blocked_reason = _enum(data.get("blocked_reason"), _BLOCK_REASONS)
    success = _bool(data.get("success"))
    policy = _object(data.get("retry_policy"))

    acceptance = _object(data.get("acceptance_result") or data.get("acceptance"))
    checks = acceptance.get("checks")
    checks = checks if isinstance(checks, list) else []
    failed_checks = []
    for index, item in enumerate(checks[:_MAX_CHECKS]):
        if not isinstance(item, dict) or item.get("passed") is not False:
            continue
        record = {"index": index, "type": _enum(item.get("type"), _CHECK_TYPES) or "unknown"}
        evidence = _object(item.get("evidence"))
        for key in ("exists", "is_file", "is_directory"):
            if isinstance(evidence.get(key), bool):
                record[key] = evidence[key]
        code = evidence.get("exit_code")
        if type(code) is int and -(2**31) <= code < 2**31:
            record["exit_code"] = code
        failed_checks.append(record)

    if data.get("needs_input") is True:
        symptom = "needs_input"
    elif check_status == "failed" or acceptance.get("passed") is False or failed_checks:
        symptom = "acceptance_failure"
    elif blocked is True:
        symptom = "blocked"
    elif success is False:
        symptom = "execution_failure"
    else:
        symptom = "unknown"

    return {
        "version": 1,
        "source": source,
        "symptom": symptom,
        "check_status": check_status,
        "blocked_reason": blocked_reason,
        "retry_eligible": _bool(policy.get("eligible")),
        "manual_retry_required": _bool(data.get("manual_retry_required")),
        "failed_checks": failed_checks,
        "checks_truncated": len(checks) > _MAX_CHECKS,
        "reproducibility": "unknown",
        "root_cause_verified": False,
    }


# Limit the historical window even if a run has unusually many attempts.
MAX_HISTORY_ATTEMPTS = 16


def _signature(observation):
    """A coarse symptom signature, never an identity of root causes."""
    symptom = observation["symptom"]
    if symptom == "blocked" and observation["blocked_reason"]:
        return ("blocked", observation["blocked_reason"])
    if symptom == "acceptance_failure" and not observation["checks_truncated"]:
        checks = observation["failed_checks"]
        if checks and all(check["type"] != "unknown" for check in checks):
            return ("acceptance_failure", json.dumps(checks, sort_keys=True))
    # Generic execution failure, missing data, and needs_input are insufficient.
    return None


def summarize_attempt_history(anchor, attempts, *, scope="latest_failed_run"):
    """Count observations of the same step within one run, not controlled trials.

    Caller must scope rows to the anchor and latest failed Run in the same
    branch. Input is newest-first, with one extra row to detect truncation.
    """
    reference = _signature(summarize_failure(anchor))
    rows = attempts[:MAX_HISTORY_ATTEMPTS]
    matching = different = unknown = checked_success = 0
    for row in rows:
        observation = summarize_failure({"state_snapshot": row.get("outputs")})
        signature = _signature(observation)
        # Attempts are bookkeeping records; a Succeeded row alone is not proof.
        result = _object(row.get("outputs"))
        if (row.get("status") == "Succeeded" and result.get("success") is True
                and result.get("check_status") == "passed"):
            checked_success += 1
        elif row.get("status") != "Failed" or reference is None or signature is None:
            unknown += 1
        elif signature == reference:
            matching += 1
        else:
            different += 1
    if matching >= 2:
        pattern = "repeated_signature_observed"
    elif matching == 1:
        pattern = "single_matching_observation"
    elif different or checked_success:
        pattern = "no_matching_observation"
    else:
        pattern = "insufficient_evidence"
    return {
        "scope": scope,
        "recorded_attempts": len(rows),
        "matching_failures": matching,
        "different_failures": different,
        "reported_checked_successes": checked_success,
        "unclassified": unknown,
        "truncated": len(attempts) > MAX_HISTORY_ATTEMPTS,
        "pattern": pattern,
        "controlled_conditions": False,
        "reproducibility": "unknown",
        "root_cause_verified": False,
    }
