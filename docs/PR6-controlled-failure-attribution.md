# PR #6 — Controlled Failure Attribution

## Status and scope

Research prototype, stacked on PR #5. This increment adds a deterministic, pure-Python analysis primitive and offline fault fixtures. It is **not** wired into automatic re-planning, retry, or the production runtime.

The objective is to distinguish:
- run-scoped failure observations;
- explicit invariant violations;
- dependency-related candidate ordering;
- ambiguity and insufficient evidence.

The analyzer never reports a verified root cause. Its output is a ranked candidate set only.

## Evidence boundary

The authoritative input is one `execution_runs` row:
- `run_id` scopes the supplied `execution_attempts`;
- `plan_id` is rechecked against every attempt;
- `branch_version` selects active, non-obsolete WorkGraph steps.

The current `step_events` schema has no `run_id`. Events can span multiple executions of the same plan and branch. PR #6 therefore counts supplied events for observability but excludes them from attribution. Adding a run foreign key to events would require a separate migration and backfill analysis; it is intentionally not part of this PR.

Attempts without a matching run ID, plan ID, or active-branch step are excluded and counted. Legacy plans with no explicit dependency edges do not get a synthetic order-based causal graph.

## Trace IR

`domain.failure_attribution.analyze_failure_attribution(run, steps, attempts, events=None)` produces:
- scope: plan, Run, and branch identifiers;
- graph: validated explicit dependency edges, or an explicit no-dependency mode;
- observations: bounded per-step attempt counts and latest failure category;
- invariant violations: status/output disagreement, acceptance disagreement, and a dependent starting before a same-Run dependency succeeded;
- attribution: candidate, ambiguous, insufficient evidence, or invalid trace;
- provenance: excluded attempt counts, truncation, and event-scope limitations.

The output projection allowlists booleans, known check types, bounded exit codes, and known blocked reasons. It does not copy raw error messages, stdout/stderr, paths, prompts, artifact contents, grant IDs, or model-generated prose. Step count, attempt count, serialized output size, and failed-check count are bounded.

## Attribution rule

1. Reject invalid Run scope or invalid WorkGraph before ranking.
2. Keep only attempts whose `run_id`, `plan_id`, and step membership match the authoritative Run and active branch.
3. Inspect the latest attempt per step. A step whose latest attempt succeeded is not treated as a current failed node merely because an earlier retry failed.
4. Record direct invariant violations from contradictory structured evidence.
5. For each dependent attempt, if its dependency also has attempts in this Run, require a successful dependency attempt finished before the dependent started. If no dependency attempt exists in this Run, prior-Run success may legitimately satisfy the dependency, so no conclusion is drawn.
6. Among currently failed steps and invariant-violation steps, suppress a downstream candidate only when an observed upstream failure/violation strictly predates the downstream observation. A graph path alone is not enough; missing or equal-resolution timestamps preserve ambiguity.
7. If the attempt window is truncated, refuse a unique attribution.

A dependency path is correlation, not proof that an upstream failure caused a downstream failure. The analyzer requires strict timestamp precedence before an observed upstream failure can suppress a downstream candidate; missing or equal-resolution timestamps do not establish order. `root_cause_verified` is always false in this prototype.

## Deterministic experiment matrix

| Case | Fixture | Expected result |
|---|---|---|
| E1 | Chain A → B → C, all fail in one Run | A is the earliest candidate; not verified |
| E2 | Two independent failed steps | Ambiguous candidates |
| E3 | Attempt belongs to another Run | Excluded; insufficient evidence |
| E4 | Branch-only step events without Run ID | Ignored for attribution |
| E5 | Attempt status contradicts output success | Direct invariant violation |
| E6 | Acceptance says passed while a check failed | Acceptance invariant violation |
| E7 | Dependent starts before same-Run dependency success | Scheduling invariant violation |
| E8 | Legacy graph has no explicit dependencies | Do not infer causality from step order |
| E9 | Missing dependency or cycle | Invalid trace; no attribution |
| E10 | Raw secret/log/path in attempt output | Must not appear in Trace IR |
| E11 | Earlier failure followed by latest successful attempt | Step is not a current failure candidate |
| E12 | Parent-step alias points to active branch clone | Dependency resolves to active clone |
| E13 | More than the attempt cap | Insufficient evidence, no unique candidate |
| E14 | Upstream failure occurs after downstream starts | Do not attribute downstream failure to the later upstream failure |
| E15 | Missing timestamps on a dependency chain | Preserve ambiguity; do not infer temporal causality |

These are deterministic unit fixtures. They validate the projection and ranking rules, not real runtime failure reproduction, model recovery effectiveness, or causal correctness.

## Acceptance criteria

- All fixtures pass offline without network, model, or external tool calls.
- Invalid graph and wrong Run scope fail closed.
- Foreign Run/plan/branch attempts do not affect attribution.
- No raw error, path, prompt, log, grant, or artifact content is copied.
- Ambiguous, temporally unordered, or truncated evidence never yields a unique candidate.
- No database schema, runtime behavior, permissions, retry policy, UI, or PR merge changes.

## Deferred work

1. Add explicit `run_id` to future step-event writes only after a migration/backfill and compatibility plan.
2. Add an adapter that reads a consistent Run/steps/attempts snapshot in one database transaction.
3. Run fault injection through the actual scheduler, not only the pure analyzer.
4. Evaluate root-cause ranking against labeled tasks and independent controls.
5. Only after those results, consider feeding candidate evidence into the diagnosis Agent. A candidate must never authorize a retry or privileged tool call.
