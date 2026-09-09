"""Pure plan-state helpers shared by API services and future runtimes."""

from typing import Iterable

TERMINAL_PLAN_STATUSES = frozenset({"Completed", "Failed", "Paused", "RolledBack"})
ACTIVE_STEP_STATUSES = frozenset({"Pending", "Running", "Retry"})
SUCCESSFUL_STEP_STATUSES = frozenset({"Success", "Skipped"})


def is_terminal(status: str | None) -> bool:
    return status in TERMINAL_PLAN_STATUSES


def can_pause(status: str | None) -> bool:
    return status in {"Running", "Pending"}


def can_resume(status: str | None) -> bool:
    return status == "Paused"


def can_skip_plan(status: str | None) -> bool:
    return status not in TERMINAL_PLAN_STATUSES


def can_skip_step(status: str | None) -> bool:
    return status in {"Pending", "Retry"}


def has_failed_step(statuses: Iterable[str]) -> bool:
    return any(status == "Failed" for status in statuses)


def can_mark_completed(statuses: Iterable[str]) -> bool:
    values = list(statuses)
    return bool(values) and not has_failed_step(values) and all(
        status in SUCCESSFUL_STEP_STATUSES for status in values
    )
