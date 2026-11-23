# Fail-Closed Execution Attempt Persistence (v1)

## Problem

The runtime previously treated both Attempt creation and terminal-state persistence as best-effort:
- if `create_execution_attempt` failed, the tool could still run with no durable Attempt row;
- if `finish_execution_attempt` returned `False`, the runtime logged and continued;
- a finished tool result could therefore advance the plan without reliable Run-scoped evidence.

This breaks the premise of failure attribution and can make an unrecorded side effect look like an ordinary retryable failure.

## Database invariants

### Attempt creation

`Database.create_execution_attempt` now uses one `INSERT ... SELECT` statement. The insert succeeds only if, at write time:

- the Run exists and belongs to the supplied plan;
- the Run is still `Running`;
- the supplied owner token matches when provided;
- no newer Run exists for the plan;
- the Step belongs to the plan, is on the requested branch, and is not obsolete.

The runtime always passes `owner_token` and `branch_version`. Optional owner tokens remain supported for legacy/test callers, but those callers still cannot create rows against a non-latest or non-running Run.

Before Attempt creation, `Database.start_step_for_run_owner` atomically moves an eligible Step to Running only when the Run is still the latest owner and the Step is active on the requested branch. The runtime no longer uses an unfenced Step status write at this boundary.

### Terminal state

The existing `finish_execution_attempt` remains the authoritative terminal write and keeps its SQL-level owner fence. The runtime now checks its boolean result rather than treating `False` as success.

If the requested terminal write raises or returns false, the runtime makes one conservative fallback attempt to mark the Attempt `Interrupted`. Whether or not that fallback succeeds, the runtime raises `AttemptPersistenceError` and does not continue scheduling.

### Step state

`Database.fail_step_for_run_owner` marks the still-Running active-branch Step as Failed only when the same Run is still the latest active owner. This avoids stale workers changing a newer Run's step state. Both the start and failure transitions are conditional SQL writes, not check-then-set sequences.

## Runtime contract

1. Atomically set the Step to Running under the current Run owner.
2. Durably create an Attempt under the current Run owner.
3. If Attempt creation fails, do not invoke the tool; attempt an owner-fenced Step failure transition and abort the Run.
4. Capture the best-effort before fingerprint.
5. Invoke the tool once.
6. Capture the best-effort after fingerprint.
7. Persist the terminal Attempt result.
8. Only after a successful terminal write may the state machine inspect the result and retry, pause, fail, or continue.
9. If terminal persistence fails, attempt the Interrupted fallback, mark the still-owned Step Failed with an outcome-unknown code, and abort. Do not replay the tool.

The tool result is not rolled back when its Attempt terminal write fails. This is intentional: replaying an operation with an unknown external side effect would be unsafe.

## Error codes

- `attempt_create_failed_before_tool_execution`: no tool call was started by this runtime path.
- `attempt_terminal_write_failed_after_tool_execution`: the tool may have run, but its final result could not be durably recorded.
- `attempt_terminal_write_failed_after_tool_exception`: the tool path raised and the Attempt failure record could not be durably completed.
- `tool_execution_exception`: the tool path raised and the Attempt failure record was persisted.

The step outputs contain only the error code, execution-outcome classification, and `automatic_retry_authorized=false`; raw database exception text is not copied into the step record.

## Validation

New deterministic tests cover:
- wrong owner token rejection at Attempt creation;
- missing/wrong-plan Step rejection;
- stale Run rejection after a newer Run is created;
- owner-fenced Step start and failure transitions;
- tool invocation count remains zero when Attempt creation fails;
- tool invocation count remains one when Attempt terminal persistence fails;
- fallback `Interrupted` persistence does not convert the operation into success.

CI must run the existing full Python, Rust, frontend test/build suites.

## Limitations

- SQLite and external tool side effects are not one atomic transaction.
- If both the requested terminal write and the Interrupted fallback fail, the Attempt may remain Running until orphan recovery; the Run is still stopped and the Step transition is attempted under the owner fence.
- This does not provide distributed leases, process-level fencing of already-running tool threads, immutable workspace restoration, or root-cause verification.
- Fingerprints remain best-effort observations, not snapshots or proof of controlled conditions.
