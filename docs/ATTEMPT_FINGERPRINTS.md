# Execution Attempt Workspace Fingerprints (v1)

## Purpose

Each execution Attempt may carry a bounded before/after workspace fingerprint in its existing `outputs._recovery_context` field. This adds diagnostic observations without a database migration or changing the tool result returned to the planner.

## Captured fields

- Git source revision.
- Hash of tracked changes relative to `HEAD`.
- Hashes of untracked file contents (bounded).
- Dependency manifest fingerprint for known Python, Node, and Rust manifests/lockfiles.
- Runtime fingerprint: Python implementation/version, OS, platform release, and machine architecture.
- Before/after workspace fingerprint and completeness/error codes.

Raw file contents, file paths, environment variables, credentials, and command output are not stored in the fingerprint.

## Bounds and fail-closed behavior

- At most 256 changed files.
- At most 8 MiB per changed file.
- At most 32 MiB total hashed bytes.
- Git metadata commands have bounded timeouts and output size.
- Deleted tracked files are represented by a fixed deletion marker.
- Symlinks, paths that resolve outside the worktree, missing/unreadable files, or any exceeded bound mark the fingerprint incomplete; symlink targets are never followed.
- If the workspace is not a Git repository, a file cannot be read safely, or any bound is exceeded, the fingerprint is marked incomplete. Incomplete fingerprints never count as matching preconditions.

Fingerprint collection is best-effort and must never prevent task execution. Attempt persistence remains fenced by the existing Run owner token.

## Interpretation

A matching fingerprint means two recorded attempts had the same observed source revision, changed-workspace manifest, dependency manifests, and runtime fingerprint. It does **not** prove that all external services, environment variables, network responses, time, or nondeterministic dependencies were equal.

A changed before/after fingerprint indicates that the workspace changed during the attempt; it does not by itself say whether the change was intended or harmful.

Even two failures with the same signature and fingerprint are observational recurrence, not a controlled experiment and not proof of a root cause. The diagnostic protocol continues to report `controlled_conditions=false`, `reproducibility=unknown`, and `root_cause_verified=false`.

## Storage

The observation is attached only to the persisted Attempt output:

`outputs._recovery_context.workspace_before`
`outputs._recovery_context.workspace_after`

It is not injected into the task's live tool result, user-facing step output, or LLM prompt as raw workspace data. The recovery summarizer emits only counts and consistency classifications.

## Not implemented

- Immutable workspace snapshots or restoration.
- Container/image, environment-variable, external-service, clock, or network capture.
- Automatic experiment execution or tool replay.
- Root-cause verification.
