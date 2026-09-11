"""Deterministic tests for the first WorkGraph dependency semantics."""

from domain.work_graph import dependency_statuses, validate_graph


def test_validate_graph_accepts_dependencies_and_legacy_alias():
    result = validate_graph([
        {"step_id": "inspect"},
        {"step_id": "verify", "dependencies": ["inspect"]},
    ])
    assert result.valid


def test_validate_graph_rejects_missing_duplicate_self_and_cycle():
    assert not validate_graph([{"step_id": "a"}, {"step_id": "a"}]).valid
    assert not validate_graph([{"step_id": "a", "depends_on": ["missing"]}]).valid
    assert not validate_graph([{"step_id": "a", "depends_on": ["a"]}]).valid
    assert not validate_graph([
        {"step_id": "a", "depends_on": ["b"]},
        {"step_id": "b", "depends_on": ["a"]},
    ]).valid


def test_dependency_statuses_ready_success_and_skip():
    step = {"step_id": "verify", "depends_on": ["a", "b"]}
    assert dependency_statuses(step, {"a": "Success", "b": "Skipped"}) == (True, False, [])
    ready, failed, blocking = dependency_statuses(step, {"a": "Running", "b": "Success"})
    assert not ready and not failed and blocking == ["a"]


def test_dependency_statuses_marks_failed_dependency_permanent():
    ready, failed, blocking = dependency_statuses(
        {"step_id": "verify", "depends_on": ["a"]}, {"a": "Failed"}
    )
    assert not ready and failed and blocking == ["a"]
