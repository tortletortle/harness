"""Deterministic, non-executing diagnostic experiment design.

This module proposes controlled observations. It never invokes tools, retries a
failed step, changes a workspace, or grants execution permission.
"""
from __future__ import annotations

from typing import Any


_ALLOWED_SYMPTOMS = {
    "acceptance_failure", "blocked", "execution_failure", "needs_input", "unknown",
}
_ALLOWED_CHECKS = {
    "file_exists", "content_contains", "exit_code", "diff_scope", "test_passes", "unknown",
}
_ALLOWED_HISTORY = {
    "repeated_signature_observed", "single_matching_observation",
    "no_matching_observation", "insufficient_evidence",
}
_ALLOWED_SIDE_EFFECTS = {"none", "reversible", "external"}


def _read(value: Any, key: str, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _source_policy(tool_spec: Any) -> dict:
    side_effect = _read(tool_spec, "side_effect")
    idempotent = _read(tool_spec, "idempotent")
    if side_effect not in _ALLOWED_SIDE_EFFECTS or not isinstance(idempotent, bool):
        return {
            "side_effect": "unknown",
            "idempotent": None,
            "sandbox_replay_candidate": False,
            "replay_authorized": False,
        }
    return {
        "side_effect": side_effect,
        "idempotent": idempotent,
        "sandbox_replay_candidate": side_effect == "none" and idempotent,
        "replay_authorized": False,
    }


def build_diagnostic_protocol(failure_evidence: Any, source_tool_spec: Any = None) -> dict:
    """Build a bounded hypothesis/experiment protocol from allowlisted evidence."""
    evidence = failure_evidence if isinstance(failure_evidence, dict) else {}
    symptom = evidence.get("symptom")
    if symptom not in _ALLOWED_SYMPTOMS:
        symptom = "unknown"
    history = evidence.get("history")
    history = history if isinstance(history, dict) else {}
    pattern = history.get("pattern")
    if pattern not in _ALLOWED_HISTORY:
        pattern = "insufficient_evidence"
    checks = evidence.get("failed_checks")
    checks = checks if isinstance(checks, list) else []
    check_types = sorted({
        item.get("type") for item in checks[:16]
        if isinstance(item, dict) and item.get("type") in _ALLOWED_CHECKS
    })
    policy = _source_policy(source_tool_spec)

    hypotheses = []
    experiments = []

    def hypothesis(hypothesis_id, priority, basis):
        hypotheses.append({
            "id": hypothesis_id,
            "priority": priority,
            "basis": basis,
            "status": "unverified",
        })

    def experiment(experiment_id, purpose, variable, controls, prerequisites, outcomes):
        experiments.append({
            "id": experiment_id,
            "purpose": purpose,
            "variable_under_test": variable,
            "controlled_variables": controls,
            "required_prerequisites": prerequisites,
            "possible_observations": outcomes,
            "execution_mode": "proposal_only",
            "readiness": "design_only_no_runner",
            "execution_blockers": ["experiment_runner_not_implemented", *prerequisites],
            "execution_authorized": False,
            "may_invoke_tools": False,
        })

    if symptom == "blocked":
        hypothesis("authorization_or_policy_boundary", "high", "persisted_block_reason")
        experiment(
            "inspect_saved_block_decision",
            "核对已持久化的拦截原因、重试策略与人工审批记录，不重新调用被拦截工具。",
            "persisted_policy_decision",
            ["plan_id", "step_id", "branch_version", "failed_run_id"],
            ["structured_block_reason", "saved_retry_policy"],
            ["字段一致：保留策略拦截结论", "字段冲突或缺失：标记证据不一致并停止自动恢复"],
        )
    elif symptom == "acceptance_failure":
        hypothesis("acceptance_contract_or_artifact_defect", "high", "failed_acceptance_checks")
        if set(check_types) & {"test_passes", "exit_code"}:
            hypothesis("test_or_runtime_environment_effect", "medium", "executable_acceptance_check")
        experiment(
            "inspect_saved_acceptance_contract",
            "只解析已保存的验收规则和失败记录，检查规则类型、字段及证据是否匹配；不运行测试。",
            "saved_acceptance_contract",
            ["saved_acceptance_spec", "saved_acceptance_result", "source_step_id"],
            ["saved_acceptance_spec", "saved_acceptance_result"],
            ["契约结构不合法：定位契约问题", "契约合法且证据一致：根因仍未确定"],
        )
        if set(check_types) & {"test_passes", "exit_code"}:
            experiment(
                "repeat_acceptance_on_frozen_snapshot",
                "在一次性隔离工作区中重复同一验收检查，判断结果是否稳定；本版本只生成方案，不执行。",
                "acceptance_invocation",
                ["source_revision", "acceptance_spec_hash", "tool_input_hash",
                 "dependency_lock_hash", "environment_fingerprint", "workspace_snapshot_id"],
                ["immutable_workspace_snapshot", "fresh_disposable_worktree_per_trial", "disposable_isolated_worktree",
                 "bounded_test_template", "explicit_human_approval"],
                ["两次结果相同：仅说明该快照下结果稳定", "结果不一致：提示非确定性或环境漂移",
                 "环境或快照不一致：实验无效，不作根因结论"],
            )
    elif symptom == "execution_failure":
        hypothesis("tool_or_runtime_failure", "medium", "structured_execution_failure")
        experiment(
            "inspect_saved_attempt_metadata",
            "对齐同一 Run 内的 Attempt 状态、退出码和已保存错误类别；不重放工具。",
            "attempt_outcome_metadata",
            ["run_id", "step_id", "attempt_no", "branch_version"],
            ["bounded_attempt_history", "structured_failure_code"],
            ["错误类别一致：形成待检验假设", "错误类别不同：按不同故障分别分析",
             "结构化记录不足：停止并补充观测"],
        )
    elif symptom == "needs_input":
        hypothesis("incomplete_user_input", "high", "runtime_needs_input_signal")
        experiment(
            "review_missing_input_boundary",
            "检查已保存的问题与步骤输入是否足以界定缺失信息；不自动猜测或填充用户输入。",
            "required_user_input",
            ["saved_step_inputs", "needs_input_question"],
            ["persisted_needs_input_record"],
            ["缺失信息明确：等待用户补充", "输入边界不明确：请求澄清，不执行步骤"],
        )
    else:
        hypothesis("insufficient_structured_evidence", "high", "unknown_failure_signature")
        experiment(
            "collect_minimal_failure_observations",
            "先补齐结构化失败码、Attempt 状态和验收记录，不触发重试。",
            "evidence_completeness",
            ["plan_id", "step_id", "run_id", "branch_version"],
            ["persisted_attempt_record", "structured_failure_code"],
            ["证据补齐：重新生成诊断协议", "仍缺失：维持 unknown 并停止自动恢复"],
        )

    if pattern == "repeated_signature_observed":
        hypothesis("stable_failure_signature", "medium", "same_run_recurrence_only")
    elif pattern == "no_matching_observation":
        hypothesis("heterogeneous_or_unmatched_failures", "medium", "different_or_unmatched_history")

    return {
        "protocol_version": "recovery-experiment-v1",
        "mode": "design_only",
        "execution_authorized": False,
        "automatic_retry_authorized": False,
        "source_tool_policy": policy,
        "history_interpretation": pattern,
        "history_is_controlled_experiment": False,
        "hypotheses": hypotheses[:4],
        "experiments": experiments[:3],
        "root_cause_verified": False,
        "reproducibility": "unknown",
        "safety_boundary": (
            "本协议只提出观察和实验设计；不得自动重试、调用工具、修改工作区或扩大 Grant。"
            "任何沙箱重放都必须先具备不可变快照、一次性隔离环境和明确人工批准。"
        ),
    }
