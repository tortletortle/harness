# Runtime boundary

## Current runtime

Python/FastAPI is the only runtime used by the local application today. The public API, SQLite persistence, executor, WebSocket stream, Skills, MCP adapter, and React dashboard all call the Python path.

## Future runtime

The Rust workspace remains the future implementation path. It is not currently wired into the Python server and must not be treated as a second production runtime until the contracts below are covered by compatibility tests.

## Migration contracts

Future Rust components must preserve:

- Plan statuses: `Draft`, `Pending`, `Approved`, `Running`, `Paused`, `Completed`, `Failed`, `RolledBack`.
- Step statuses: `Pending`, `Running`, `Success`, `Failed`, `Retry`, `Skipped`, `RolledBack`.
- Existing JSON schemas in `schemas/` for design documents, execution plans, and tools.
- The rule that a plan with an active failed step cannot be reported as `Completed`.
- Pause/resume checkpoint semantics and branch-aware rollback behavior.
- API error semantics and the existing WebSocket event names.

Schema migrations and the AgentSession/Run/Turn/Attempt persistence model are intentionally deferred until the service and repository boundaries have been validated in the Python runtime.
