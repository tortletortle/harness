import pytest

from python.domain.plan_state import (
    can_mark_completed,
    can_pause,
    can_resume,
    can_skip_plan,
    can_skip_step,
    has_failed_step,
    is_terminal,
)


@pytest.mark.parametrize("status", ["Completed", "Failed", "Paused", "RolledBack"])
def test_terminal_statuses_are_not_active(status):
    assert is_terminal(status)
    assert not can_skip_plan(status)


def test_pause_and_resume_guards_match_runtime_contract():
    assert can_pause("Running")
    assert can_pause("Pending")
    assert not can_pause("Completed")
    assert can_resume("Paused")
    assert not can_resume("Failed")


def test_skip_step_only_allows_pending_or_retry():
    assert can_skip_step("Pending")
    assert can_skip_step("Retry")
    assert not can_skip_step("Running")
    assert not can_skip_step("Success")


def test_failed_steps_cannot_be_reported_completed():
    assert has_failed_step(["Success", "Failed"])
    assert not can_mark_completed(["Success", "Failed"])
    assert can_mark_completed(["Success", "Skipped"])
