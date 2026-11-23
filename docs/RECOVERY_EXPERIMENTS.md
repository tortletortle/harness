# Controlled Diagnostic Experiments (v1)

## Purpose

The recovery pipeline now distinguishes three different things:

1. **Observed symptom** — a bounded projection of a persisted failed step.
2. **Repeated signature** — similar structured failure observations within one failed Run.
3. **Verified cause** — a causal conclusion supported by a controlled experiment.

The first two do not establish the third. The protocol in `python/domain/recovery_experiments.py` designs the next observation; it does not run it.

## API

`GET /api/plan/{plan_id}/diagnostic-plan`

- Requires the normal API token.
- Only accepts a plan whose current recovery anchor is Failed.
- Reads one consistent replan snapshot.
- Returns allowlisted failure evidence and a deterministic diagnostic protocol.
- Does not call the LLM, invoke a tool, create a Run/Attempt, change a step, or write an event.

The existing `POST /api/plan/{plan_id}/replan` also supplies this protocol to the repair planner as untrusted observation context. It is not an execution request.

## Experiment design

For a failed deterministic acceptance check, the first proposed controlled experiment is:

- **Question:** does the same acceptance check produce the same result under frozen conditions?
- **Variable under test:** trial/invocation.
- **Held constant:** source revision, acceptance-spec hash, tool-input hash, dependency lock hash, environment fingerprint, and immutable workspace snapshot.
- **Isolation:** each trial must start from a fresh disposable worktree restored from the same snapshot, so one test cannot contaminate the next.
- **Interpretation:** identical results show stability only for that captured snapshot and environment; differing results suggest nondeterminism or incomplete environmental control. Neither result alone proves a root cause.

A second comparison may change exactly one factor (for example, original failed workspace state versus a clean worktree) while preserving the source revision, inputs, acceptance specification, dependencies, and environment. This is not currently executable because the runtime does not yet persist all required immutable snapshot/fingerprint identifiers.

## Safety

Every experiment has `execution_mode=proposal_only`, `execution_authorized=false`, and `may_invoke_tools=false`.

- No automatic retry or tool replay.
- No permission or Grant expansion.
- No edits to the failed workspace.
- A future sandboxed test replay requires an immutable snapshot, a disposable isolated worktree per trial, bounded test templates, and explicit human approval.
- Unknown tool metadata is treated as external/non-idempotent for replay eligibility.
- A capability's `side_effect` and `idempotent` metadata are hints for future admission control, never authorization.

## Current limitations

- The protocol is deterministic rule-based planning, not a learned causal model.
- The runtime does not yet capture immutable workspace snapshot IDs, source revision fingerprints, dependency lock hashes, or a complete environment fingerprint for every Attempt.
- No experiment runner is implemented. The endpoint is a read-only design surface.
- Recurrence counts remain scoped to one step and the latest failed Run; recurrence is not a controlled reproduction.
- The repair planner receives the protocol but must still produce ordinary validated repair steps; it cannot execute the protocol.

## Validation

The v1 tests cover symptom-specific protocol generation, single-variable experiment design, missing/unknown tool metadata, prompt-injection string exclusion, endpoint read-only behavior, and rejection of non-Failed plans.
