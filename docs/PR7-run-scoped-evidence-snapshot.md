# PR #7 — Run-scoped Evidence Snapshot

## Status

Research implementation, stacked on PR #6. This PR freezes the active WorkGraph at Run creation and adds a bounded, read-only SQLite snapshot reader. It does not yet claim a complete end-to-end trace.

## Why this increment is necessary

The current plan/steps tables are mutable. A Run stores only `plan_id` and `branch_version`; re-planning, branch cloning, or step edits can make a later diagnostic read a graph that differs from the graph actually executed.

The existing database also has an important evidence boundary:
- `execution_attempts` already has `run_id`;
- `artifacts` and `agent_sessions` already have `run_id`;
- `step_events` and `audit_logs` do not have `run_id`;
- `artifacts` and `agent_sessions` do not have an Attempt-level producer link;
- Attempt creation is currently best-effort in the execution loop.

PR #7 does not infer missing associations from timestamps, step IDs, or plan IDs.

## Schema migration

Alembic revision `0002_run_graph_snapshot` adds nullable columns to `execution_runs`:
- `graph_snapshot_json`
- `graph_snapshot_hash`

The runtime DDL in `database.py` is kept in schema parity with Alembic. `scripts/verify_migrations.py` is added to CI so the Alembic-built schema and runtime-built schema are compared on every change.

Existing Runs retain NULL snapshot fields. Do not backfill from the current mutable steps table. Existing deployments must back up the database and apply `python -m alembic upgrade head`; if not yet stamped, stamp `0001_baseline` first.

## Immutable WorkGraph snapshot

`start_execution_run` captures the active non-obsolete branch inside the same SQLite write transaction that claims the plan and inserts the Run.

The canonical JSON contains only:
- schema version, plan ID, branch version and capture time;
- step ID, order, tool identifier;
- explicit dependencies and parent-step alias;
- bounded step count and graph validity status.

It deliberately excludes step inputs, prompts, check criteria prose, outputs, artifact URIs, secrets, and model text.

The JSON is canonicalized with sorted keys and compact separators, then SHA-256 hashed. The snapshot cap is 256 steps to match PR #6's analyzer limit. If the graph is empty, malformed, cyclic, ambiguous, or exceeds the cap, it is marked invalid/incomplete rather than silently truncated into a valid graph.

## Snapshot reader

`Database.get_failure_attribution_snapshot(run_id)`:
1. Opens one SQLite connection and starts one read transaction.
2. Reads the authoritative Run row and verifies the stored graph hash and scope.
3. Reads Run-scoped Attempts, Artifacts, Agent Sessions, and their Turns.
4. Uses bounded `LIMIT + 1` reads to detect truncation.
5. Checks Attempt timestamps against Run start/finish and flags unfinished Attempts at Run end.
6. Counts legacy plan/branch-scoped `step_events` and `audit_logs`, but does not return their raw payloads or attribute them to this Run.
7. Omits audit instructions/inputs, event payloads, artifact URIs/metadata, and session error text.

The returned top-level `run`, `steps`, `attempts`, and `events` fields are shaped for the PR #6 local analyzer. Attempt output strings above 128 KiB are omitted in SQL and flagged by Attempt ID; this also blocks `core_analysis_ready`. Smaller raw Attempt `outputs` may still contain sensitive text and must remain local; call the PR #6 allowlist projection before any model-facing use.

## Completeness semantics

This PR intentionally distinguishes:

- `core_analysis_ready`: the frozen graph is valid, the bounded Attempt window is not truncated, timestamps are consistent, and the Run is terminal Failed.
- `analysis_ready`: **false in this PR**. The execution loop still treats Attempt creation as best-effort, so a read snapshot cannot prove that no Attempt insert was lost.
- Oversized Attempt output: any `outputs` string above 128 KiB is omitted before materialization and blocks `core_analysis_ready`.
- `full_trace_ready`: **false in this PR**. Events and audits are still unscoped; artifact and session Attempt lineage is unavailable.

The snapshot is therefore suitable for controlled offline structural analysis, not yet for autonomous recovery or production root-cause claims.

## Deterministic acceptance tests

`tests/python/test_failure_attribution_snapshot.py` covers:
- canonical graph capture and hash (the hash detects accidental mismatch; it is not an authenticity signature);
- graph immutability after live plan mutation;
- terminal Run and Attempt lifecycle checks;
- `LIMIT + 1` truncation detection;
- unscoped legacy events/audits counted but not attached or leaked;
- old Runs without graph snapshots fail closed;
- missing Run returns no snapshot.

CI must also run `python scripts/verify_migrations.py` and require `SCHEMA_IDENTICAL`.

## Deferred to PR #8

1. Add nullable `run_id` and `attempt_id` to new `step_events` and `audit_logs` writes.
2. Add `attempt_id` to Artifacts and Agent Sessions; propagate context through `step_execution`, Executor, AgentRuntimeContext, and CodingAgent.
3. Add explicit Artifact-consumption edges so downstream Attempt lineage identifies the exact upstream Artifact IDs/hashes it consumed.
4. Harden Attempt persistence: do not execute a tool when its Attempt cannot be durably created; stop further scheduling if terminal Attempt persistence fails.
5. Build real scheduler fault-injection tests and only then promote `analysis_ready`.

## Non-goals and safety boundary

- No LLM diagnosis, autonomous retry, recovery action, permission expansion, UI change, or schema backfill.
- A unique candidate is not a verified root cause.
- No event/audit row is attached to a Run by timestamp proximity.
- No historical graph is reconstructed from current plan state.
- No automatic merge.
